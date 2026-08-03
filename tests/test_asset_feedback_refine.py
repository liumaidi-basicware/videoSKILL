import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import asset_prep  # noqa: E402
import key_setup  # noqa: E402
import br_client  # noqa: E402


class AssetFeedbackRefineTests(unittest.TestCase):
    def test_feedback_reference_is_sent_without_becoming_an_asset(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "candidate.png")
            feedback = os.path.join(root, "feedback.png")
            open(source, "wb").close()
            open(feedback, "wb").close()
            calls = []
            with mock.patch.object(key_setup, "load_key", return_value="sk-test"), \
                 mock.patch.object(br_client, "to_image_ref",
                                   side_effect=lambda path: "ref:" + path), \
                 mock.patch.object(asset_prep, "_load_brief", return_value={"images": []}), \
                 mock.patch.object(asset_prep, "_save_brief"), \
                 mock.patch.object(asset_prep, "_create_one",
                                   side_effect=lambda *args: calls.append(args) or "http://image") as create, \
                 mock.patch.object(br_client, "download"):
                with mock.patch.object(asset_prep, "_save_image", return_value={"status": "pending"}) as save:
                    asset_prep.refine_image("acme", source, "把耳机正确夹在耳廓外侧",
                                            feedback_ref=feedback)
            refs = create.call_args.args[2]
            self.assertEqual(refs, ["ref:" + source, "ref:" + feedback])
            prompt = save.call_args.args[3]
            self.assertIn("只修改客户明确指出的问题", prompt)
            self.assertEqual(save.call_args.kwargs["extra"]["feedback_refs"], [
                os.path.abspath(feedback)])

    def test_multiple_uploaded_feedback_images_are_submitted_as_references(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "candidate.png")
            feedback_a = os.path.join(root, "wearing.png")
            feedback_b = os.path.join(root, "angle.png")
            for path in (source, feedback_a, feedback_b):
                open(path, "wb").close()
            with mock.patch.object(key_setup, "load_key", return_value="sk-test"), \
                 mock.patch.object(br_client, "to_image_ref", side_effect=lambda path: "ref:" + path), \
                 mock.patch.object(asset_prep, "_load_brief", return_value={"images": []}), \
                 mock.patch.object(asset_prep, "_create_one", return_value="http://image") as create, \
                 mock.patch.object(asset_prep, "_save_image", return_value={"status": "pending"}) as save:
                asset_prep.refine_image("acme", source, "修正人物佩戴方式",
                                        feedback_ref=[feedback_a, feedback_b])
            self.assertEqual(create.call_args.args[2], [
                "ref:" + source, "ref:" + feedback_a, "ref:" + feedback_b])
            self.assertEqual(save.call_args.kwargs["extra"]["feedback_refs"], [
                os.path.abspath(feedback_a), os.path.abspath(feedback_b)])


if __name__ == "__main__":
    unittest.main()
