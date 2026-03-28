"""Gemini 2.5 Flash face analysis + tag-based glasses recommendation engine."""

import json
import re
import time

from config import resolve_analysis_model
from analysis_prompt import build_analysis_prompt
from utils import load_image_as_part


class AnalysisResult:
    """Result of a face analysis operation."""

    def __init__(
        self,
        success: bool,
        analysis: dict | None = None,
        model_used: str = "",
        elapsed_seconds: float = 0.0,
        error: str | None = None,
        raw_response: str | None = None,
    ):
        self.success = success
        self.analysis = analysis
        self.model_used = model_used
        self.elapsed_seconds = elapsed_seconds
        self.error = error
        self.raw_response = raw_response


class FaceAnalyzer:

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash", client=None):
        from google import genai
        self.client = client if client is not None else genai.Client(api_key=api_key)
        self.model = resolve_analysis_model(model)

    def analyze(self, portrait_path: str) -> AnalysisResult:
        """
        Send portrait to Gemini 2.5 Flash for face analysis + tag-based recommendation.

        Args:
            portrait_path: Path to the portrait image.

        Returns:
            AnalysisResult with parsed analysis dict on success.
        """
        prompt = build_analysis_prompt()

        # Load image as Part
        try:
            portrait_part = load_image_as_part(portrait_path)
        except (ValueError, FileNotFoundError) as e:
            return AnalysisResult(
                success=False,
                error=f"Image loading error: {e}",
            )

        # Send to Gemini 2.5 Flash (vision)
        # Image first, then text prompt — this order works best for analysis
        # Disable thinking: structured JSON output doesn't benefit from it,
        # and the default "auto" budget adds 10-30s of unnecessary latency.
        from google.genai import types as genai_types
        start_time = time.time()

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[portrait_part, prompt],
                config=genai_types.GenerateContentConfig(
                    temperature=0,
                    thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
                ),
            )
        except Exception as e:
            elapsed = time.time() - start_time
            error_msg = str(e)

            if "safety" in error_msg.lower() or "blocked" in error_msg.lower():
                error_msg = (
                    "The portrait was blocked by safety filters. "
                    "Try a different photo.\n"
                    f"Details: {error_msg}"
                )
            elif "rate" in error_msg.lower() or "429" in error_msg:
                error_msg = (
                    f"Rate limited. Please wait and try again.\n"
                    f"Details: {error_msg}"
                )
            elif "api key" in error_msg.lower() or "401" in error_msg or "403" in error_msg:
                error_msg = (
                    f"API key issue. Check your GEMINI_API_KEY.\n"
                    f"Details: {error_msg}"
                )

            return AnalysisResult(
                success=False,
                model_used=self.model,
                elapsed_seconds=elapsed,
                error=error_msg,
            )

        elapsed = time.time() - start_time

        # Parse JSON from response
        raw_text = response.text
        analysis, parse_error = self._parse_json_response(raw_text)

        if parse_error:
            # Retry once — sometimes the model wraps JSON in explanation text
            try:
                retry_prompt = prompt + (
                    "\n\nIMPORTANT: Your response must be ONLY a valid JSON object. "
                    "No markdown code fences, no explanation text. Just the raw JSON."
                )
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=[portrait_part, retry_prompt],
                    config=genai_types.GenerateContentConfig(
                        temperature=0,
                        thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
                    ),
                )
                raw_text = response.text
                analysis, parse_error = self._parse_json_response(raw_text)
            except Exception:
                pass

        if parse_error:
            return AnalysisResult(
                success=False,
                model_used=self.model,
                elapsed_seconds=elapsed,
                error=f"Failed to parse analysis JSON: {parse_error}",
                raw_response=raw_text,
            )

        # Validate required keys
        validation_error = self._validate_analysis(analysis)
        if validation_error:
            return AnalysisResult(
                success=False,
                model_used=self.model,
                elapsed_seconds=elapsed,
                error=validation_error,
                raw_response=raw_text,
            )

        return AnalysisResult(
            success=True,
            analysis=analysis,
            model_used=self.model,
            elapsed_seconds=elapsed,
            raw_response=raw_text,
        )

    def _parse_json_response(self, text: str) -> tuple[dict | None, str | None]:
        """
        Parse JSON from the model response, handling markdown fences.
        Returns (parsed_dict, error_message).
        """
        text = text.strip()

        # Strip markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?\s*```$", "", text)
        text = text.strip()

        try:
            return json.loads(text), None
        except json.JSONDecodeError as e:
            # Try to extract JSON object from within the text
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                try:
                    return json.loads(match.group()), None
                except json.JSONDecodeError:
                    pass
            return None, str(e)

    def _validate_analysis(self, analysis: dict) -> str | None:
        """
        Validate that the analysis has the required structure.
        Returns error message or None if valid.
        """
        if "face_analysis" not in analysis:
            return "Missing 'face_analysis' in response"
        if "glasses_recommendation" not in analysis:
            return "Missing 'glasses_recommendation' in response"

        fa = analysis["face_analysis"]
        rec = analysis["glasses_recommendation"]

        # Check critical face analysis fields
        required_fa = ["face_shape", "nose", "eyes", "skin", "hair"]
        for field in required_fa:
            if field not in fa:
                return f"Missing face_analysis.{field}"

        # Check recommendation has recommended_tags (new schema)
        if "recommended_tags" not in rec:
            return "Missing glasses_recommendation.recommended_tags"
        if "reasoning" not in rec:
            return "Missing glasses_recommendation.reasoning"

        # Validate recommended_tags structure
        tags = rec["recommended_tags"]
        for category in ("frame", "lenses", "style"):
            if category not in tags:
                return f"Missing recommended_tags.{category}"

        # Fill optional fields with defaults if missing
        fa.setdefault("facial_hair", {
            "present": False,
            "type": "none",
            "style": None,
        })
        fa.setdefault("overall_proportions", {
            "face_width_to_height_ratio": "not determined",
            "symmetry": "moderate",
            "facial_thirds": "not determined",
        })
        fa.setdefault("estimated_age_range", "unknown")
        fa.setdefault("estimated_gender_presentation", "unknown")
        fa.setdefault("photo_context", {
            "lighting": "natural_indoor",
            "setting": "unknown",
            "face_angle": "straight_on",
        })

        # Fill optional reasoning fields
        reasoning = rec["reasoning"]
        reasoning.setdefault("lens_logic", "")

        # Fill optional face_insights with defaults if missing
        if "face_insights" not in analysis:
            analysis["face_insights"] = []
        elif not isinstance(analysis["face_insights"], list):
            analysis["face_insights"] = []

        # Fill optional face_summary with defaults if missing
        if "face_summary" not in analysis or not isinstance(analysis["face_summary"], dict):
            analysis["face_summary"] = {}
        fs = analysis["face_summary"]
        fs.setdefault("face_shape", "")
        fs.setdefault("face_shape_description", "")
        fs.setdefault("key_geometry", "")
        fs.setdefault("key_geometry_description", "")
        fs.setdefault("color_profile", "")
        fs.setdefault("color_profile_description", "")
        fs.setdefault("hair_color_hex", "#2A2A2A")
        fs.setdefault("eye_color_hex", "#3E2723")
        fs.setdefault("skin_tone_hex", "#EAC0A2")

        return None
