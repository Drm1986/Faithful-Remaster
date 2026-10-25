import unittest
from material_aware import classify, plan_texture, plan_manifest

class MaterialAwareTests(unittest.TestCase):
    def test_material(self):
        self.assertEqual(classify("palace_stone_wall.png")[0], "mixed")
        self.assertEqual(classify("forest_leaves.png")[0], "foliage")
        self.assertEqual(classify("6ad93cfee1.png")[0], "unknown")
    def test_ui_protection(self):
        r = plan_texture("main_hud.png", 256, 128)
        self.assertEqual(r.mode, "structural_only")
        self.assertEqual(r.denoise_cap, 0)
    def test_atlas_animation(self):
        r = plan_texture("wood.png", 64, 32, atlas=True, animated=True)
        self.assertEqual(r.denoise_cap, 0)
        self.assertTrue(r.requires_review)
    def test_aspect_and_limit(self):
        r = plan_texture("wood.png", 512, 256)
        self.assertEqual((r.target_width, r.target_height), (4096, 2048))
    def test_alpha_and_seam(self):
        r = plan_texture("grass.png", 128, 128, has_alpha=True, seamless=True)
        self.assertTrue(r.requires_review)
    def test_low_confidence(self):
        r = plan_texture("7a.png", 128, 128)
        self.assertTrue(r.requires_review)
    def test_presets(self):
        a = plan_texture("rock.png", 256, 256)
        b = plan_texture("rock.png", 256, 256, preset="Strong Believer")
        self.assertGreater(b.denoise_cap, a.denoise_cap)
    def test_manifest(self):
        result = plan_manifest([{"filename":"wood.png","width":128,"height":64}])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["coherence_group"], "default:wood")
    def test_validation(self):
        with self.assertRaises(ValueError):
            plan_texture("rock.png", 0, 32)

if __name__ == "__main__":
    unittest.main()
