import json
import logging
import os
from typing import List
from google import genai
from google.genai import types
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Initialize Gemini client
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", "default_key"))


class AIValidationResult(BaseModel):
    success: bool
    waste_category: str
    waste_sub_type: str
    reason_code: str


def validate_disposal_with_ai_video(video_bytes: bytes) -> dict:
    """
    Validate disposal action using Gemini AI video analysis
    Returns dict with success, waste_category, waste_sub_type, and reason_code
    """
    try:
        # Use the exact auditor prompt from specifications (updated for video)
        system_prompt = """You are a strict, automated AI Auditor for the "TrackEco" environmental game. Your function is to analyze a video recording and return a structured JSON object based on the rigorous protocol below. You are a classifier, not a game designer; do not make subjective judgments, simply report the facts based on the protocol.

<protocol>
<step_1 title="Veto Audit: Is the action fundamentally invalid?">
    A. **Analyze Environmental Outcome:** Does the object end up in a valid waste receptacle? If it lands in a natural environment (bushes, water, etc.), this is an IMMEDIATE VETO. The reason code is `FAIL_LITTERING`.
    B. **Analyze Object Usability:** Does the object appear to be new, full, or perfectly functional (e.g., a full water bottle, a usable toy)? If yes, this is an IMMEDIATE VETO. The reason code is `FAIL_WASTE_USABLE`.
    C. **If either A or B is a veto, stop here.**
</step_1>

<step_2 title="Object Identification & Classification">
    A. **Identify Primary Category:** First, classify the waste into one of the 7 official `waste_category` values.
    B. **Identify Specific Sub-Type:** Second, identify the most specific `waste_sub_type` from the official list that matches the object. This is the most critical step for the game's variety mechanic.
    C. **Check for Significance:** If the object is too small to be meaningful (e.g., smaller than a credit card), you MUST classify its `waste_sub_type` as "Insignificant Debris". This is a failure condition.
</step_2>
</protocol>

<response_format>
Respond with ONLY a valid JSON object. Do not include any other text. The JSON must have these four keys:

{
  "success": <boolean>,
  "waste_category": <string from the official categories list>,
  "waste_sub_type": <string from the official sub-types list>,
  "reason_code": <string>
}

**Official Waste Categories & Sub-Types List:**

*   **Category: "Plastic"**
    *   Sub-Types: "PET Bottle", "HDPE Jug", "Plastic Bag", "Food Wrapper", "Plastic Cutlery", "Styrofoam", "Plastic Container", "Other Plastic"
*   **Category: "Paper/Cardboard"**
    *   Sub-Types: "Cardboard Box", "Newspaper/Magazine", "Office Paper", "Paper Cup/Plate", "Paper Bag", "Other Paper"
*   **Category: "Glass"**
    *   Sub-Types: "Glass Bottle", "Glass Jar", "Other Glass"
*   **Category: "Metal"**
    *   Sub-Types: "Aluminum Can", "Steel Can", "Aluminum Foil", "Scrap Metal", "Other Metal"
*   **Category: "Organic"**
    *   Sub-Types: "Food Scraps", "Yard Waste"
*   **Category: "E-Waste"**
    *   Sub-Types: "Battery", "Cable/Wire", "Small Electronic", "Other E-Waste"
*   **Category: "General Waste"**
    *   Sub-Types: "Fabric/Clothing", "Mixed Material Packaging", "Hygiene Product", "Other General Waste"
*   **Category: "Insignificant"**
    *   Sub-Types: "Insignificant Debris"

**Reason Code Reference:**
- If successful: `reason_code`: "SUCCESS"
- If failed: `reason_code`: "FAIL_LITTERING", "FAIL_WASTE_USABLE", "FAIL_OBJECT_TOO_SMALL", or "FAIL_UNCLEAR".
</response_format>"""

        # Prepare content with video
        content_parts = []

        # Add video to content
        content_parts.append(
            types.Part.from_bytes(data=video_bytes, mime_type="video/webm"))

        # Add instruction
        content_parts.append(
            "Analyze this video showing a waste disposal action and classify according to the protocol."
        )

        # Call Gemini API
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=content_parts,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=AIValidationResult,
                temperature=0.1  # Low temperature for consistent classification
            ))

        if not response.text:
            logger.error("Empty response from Gemini")
            return {
                "success": False,
                "waste_category": "General Waste",
                "waste_sub_type": "Other General Waste",
                "reason_code": "FAIL_UNCLEAR"
            }

        # Parse JSON response
        try:
            result = json.loads(response.text)
            logger.info(f"AI validation result: {result}")

            # Validate required fields
            required_fields = [
                "success", "waste_category", "waste_sub_type", "reason_code"
            ]
            if not all(field in result for field in required_fields):
                raise ValueError("Missing required fields in AI response")

            # Handle insignificant debris as failure
            if result.get("waste_sub_type") == "Insignificant Debris":
                result["success"] = False
                result["reason_code"] = "FAIL_OBJECT_TOO_SMALL"

            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response as JSON: {e}")
            logger.error(f"Raw response: {response.text}")
            return {
                "success": False,
                "waste_category": "General Waste",
                "waste_sub_type": "Other General Waste",
                "reason_code": "FAIL_UNCLEAR"
            }

    except Exception as e:
        logger.error(f"Error validating disposal with AI: {str(e)}")
        return {
            "success": False,
            "waste_category": "General Waste",
            "waste_sub_type": "Other General Waste",
            "reason_code": "FAIL_UNCLEAR"
        }
