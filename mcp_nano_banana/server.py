"""FastMCP server for Google Nano Banana image generation."""

import base64
import io
import os
import sys

from fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP(
    name="nano-banana",
    version="1.0.0",
    instructions="Generate images using Google's Nano Banana (Gemini 2.5 Flash Image) model."
)


@mcp.tool
def generate_image(prompt: str, model: str = "gemini-2.5-flash-image") -> dict:
    """
    Generate an image using Google's Nano Banana (Gemini 2.5 Flash Image) model.
    
    Args:
        prompt: Text description of the image to generate
        model: Model to use for image generation (default: "gemini-2.5-flash-image")
    
    Returns:
        Dictionary with base64-encoded image data and metadata
    """
    try:
        # Import here to avoid dependency issues if not installed
        from google import genai
        
        # Get API key from environment
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return {
                "error": True,
                "message": "GEMINI_API_KEY environment variable is not set"
            }
        
        # Initialize Gemini client
        client = genai.Client(api_key=api_key)
        
        # Generate image
        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )
        
        # Extract image from response
        image_data = None
        image_mime_type = "image/png"  # Default
        
        # Check response parts for image data
        # According to Gemini API docs, response.parts contains parts with inline_data
        for part in response.parts:
            if hasattr(part, "inline_data") and part.inline_data:
                # Convert to PIL Image
                try:
                    image = part.as_image()
                    # Convert PIL Image to bytes
                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG")
                    image_data = buffer.getvalue()
                    image_mime_type = "image/png"
                    break
                except Exception as e:
                    # Fallback: try to get raw data
                    if hasattr(part.inline_data, "data"):
                        image_data = part.inline_data.data
                        if hasattr(part.inline_data, "mime_type"):
                            image_mime_type = part.inline_data.mime_type
                        break
        
        if not image_data:
            return {
                "error": True,
                "message": "No image data found in response from Gemini API"
            }
        
        # Convert image data to base64
        if isinstance(image_data, bytes):
            image_base64 = base64.b64encode(image_data).decode("utf-8")
        else:
            # Try to encode
            image_base64 = base64.b64encode(bytes(image_data)).decode("utf-8")
        
        # Return result with image data
        return {
            "image_base64": image_base64,
            "mime_type": image_mime_type,
            "prompt": prompt,
            "model": model
        }
        
    except ImportError:
        return {
            "error": True,
            "message": "google-genai package is not installed. Install it with: pip install google-genai"
        }
    except Exception as e:
        error_msg = str(e)
        print(f"[ERROR] Nano Banana image generation failed: {error_msg}", file=sys.stderr)
        sys.stderr.flush()
        return {
            "error": True,
            "message": f"Image generation failed: {error_msg}"
        }


# Main entry point is in __main__.py for module execution
# This allows running as: python -m mcp_nano_banana

