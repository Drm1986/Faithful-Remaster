"""Conservative, dependency-free material-aware recipe planner for Faithful Remaster.

Opt-in planning layer; does not modify pixels or interfere with existing pipelines.
Semantic categories inferred from filename and optional trusted classifier predictions.
Unknown/mixed/UI inputs use conservative recipes by default.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import json
import re
from pathlib import Path

MATERIALS = {
    "stone": ("stone", "rock", "brick", "masonry", "granite", "marble"),
    "plaster": ("wall", "plaster", "stucco", "paintedwall"),
    "wood": ("wood", "timber", "plank", "bark"),
    "foliage": ("leaf", "leaves", "tree", "foliage", "grass", "bush", "moss"),
    "metal": ("metal", "iron", "steel", "bronze", "copper"),
    "fabric": ("fabric", "cloth", "carpet", "curtain", "robe"),
    "skin": ("skin", "face", "hand", "portrait"),
    "ground": ("ground", "sand", "soil", "dirt", "terrain"),
    "water_sky": ("water", "sky", "cloud", "river", "ocean"),
    "ui": ("ui", "hud", "font", "text", "icon", "menu", "button"),
}
SAFE_STRENGTH = {
    "stone": 0.18, "plaster": 0.12, "wood": 0.15,
    "foliage": 0.12, "metal": 0.10, "fabric": 0.12,
    "skin": 0.06, "ground": 0.15, "water_sky": 0.08,
    "ui": 0.0, "mixed": 0.0, "unknown": 0.06,
}
DETAIL = {
    "stone": "subtle mineral grain; preserve existing cracks and painted edges",
    "plaster": "restrained surface grain; preserve hand-painted gradients",
    "wood": "follow existing wood grain direction; no invented planks",
    "foliage": "preserve leaf clusters and silhouettes; no photographic leaves",
    "metal": "preserve painted reflections; no new specular highlights",
    "fabric": "subtle weave following existing folds; no added patterns",
    "skin": "retain original facial features and stylized shading",
    "ground": "fine-scale ground grain; preserve original paths and marks",
    "water_sky": "preserve painted gradients and cloud/wave shapes",
    "ui": "do not use generative detail reconstruction",
    "mixed": "avoid material-specific generation",
    "unknown": "preserve original shapes without inventing materials",
}

@dataclass(frozen=True)
class MaterialRecipe:
    material: str
    confidence: float
    mode: str
    denoise_cap: float
    target_width: int
    target_height: int
    detail_instruction: str
    negative_instruction: str
    coherence_group: str
    requires_review: bool
    reasons: tuple[str, ...]

    def to_dict(self):
        result = asdict(self)
        result["reasons"] = list(self.reasons)
        return result


def _tokens(filename):
    # Emulator hashes are not descriptions; no positive match means unknown.
    return set(t for t in re.split(r"[^a-z0-9]+", Path(filename).stem.lower()) if t)


def classify(filename, predictions=None):
    """Return material, confidence. Predictions: optional {category: 0..1}."""
    if predictions:
        scores = sorted(
            ((str(k), float(v)) for k, v in predictions.items()
             if k in MATERIALS and isinstance(v, (int, float)) and 0 <= v <= 1),
            key=lambda x: x[1], reverse=True)
        if scores:
            if len(scores) > 1 and scores[0][1] - scores[1][1] < 0.12:
                return "mixed", scores[0][1]
            if scores[0][1] >= 0.70:
                return scores[0]
    tokens = _tokens(filename)
    hits = [kind for kind, terms in MATERIALS.items() if tokens.intersection(terms)]
    if len(hits) == 1:
        return hits[0], 0.75
    if len(hits) > 1:
        return "mixed", 0.45
    return "unknown", 0.0


def plan_texture(filename, width, height, *, predictions=None, has_alpha=False,
                 atlas=False, animated=False, seamless=False, game_style=None,
                 preset="Clean Heart", max_side=4096):
    """Produce advisory recipe. Never auto-apply to ComfyUI without an opt-in adapter."""
    if width < 1 or height < 1 or max_side < 1:
        raise ValueError("Texture dimensions and max_side must be positive")
    material, confidence = classify(filename, predictions)
    reasons = []
    protected = bool(atlas or animated or material == "ui")
    if atlas: reasons.append("atlas: protect independently mapped regions")
    if animated: reasons.append("animated: keep frame-to-frame consistency")
    if has_alpha: reasons.append("alpha: retain exact mask and edges")
    if seamless: reasons.append("tileable: require wraparound seam check")
    if confidence < 0.70: reasons.append("low semantic confidence")
    if protected: reasons.append("generative pass disabled")
    # Uniform scale preserves original aspect ratio; do not force square textures.
    scale = max(1, min(16, max_side // max(width, height)))
    mode = "structural_only" if protected or material == "mixed" else "guided_detail"
    strength = SAFE_STRENGTH[material]
    if preset.lower().strip() == "strong believer" and not protected:
        strength = min(0.24, strength + 0.04)
    if mode == "structural_only":
        strength = 0.0
    style = (game_style or {}).get("style_instruction", "preserve the original game's artistic identity")
    group = (game_style or {}).get("game_id", "default")
    negative = ("photorealistic reinterpretation, new objects, invented text, palette drift, "
                "new highlights, baked shadows, inconsistent texture scale")
    return MaterialRecipe(
        material, confidence, mode, round(strength, 3), width * scale, height * scale,
        f"{DETAIL[material]}; {style}; keep source palette and silhouettes",
        negative, f"{group}:{material}", protected or confidence < 0.70 or has_alpha or seamless,
        tuple(reasons))


def plan_manifest(items, *, game_style=None, preset="Clean Heart"):
    """Plan a batch; group recipes for coherent review before sending to ComfyUI."""
    return [plan_texture(item["filename"], item["width"], item["height"],
                         predictions=item.get("predictions"),
                         has_alpha=item.get("has_alpha", False),
                         atlas=item.get("atlas", False),
                         animated=item.get("animated", False),
                         seamless=item.get("seamless", False),
                         game_style=game_style, preset=preset).to_dict()
            for item in items]


def save_manifest(path, items, **kwargs):
    output = {"schema_version": 1, "advisory_only": True,
              "recipes": plan_manifest(items, **kwargs)}
    Path(path).write_text(json.dumps(output, indent=2), encoding="utf-8")
    return output
