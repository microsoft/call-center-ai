"""
Custom LLM tools specific to Jay's Frames framing business.

These tools extend the base LLM capabilities with business-specific functions
like price estimation, frame recommendations, and knowledge base search.
"""

from typing import Annotated

from app.helpers.llm_tools import DefaultPlugin, add_customer_response


class JaysFramesPlugin(DefaultPlugin):
    """
    Extended plugin for Jay's Frames with custom framing tools.

    To use this plugin, update the LLM configuration to use JaysFramesPlugin
    instead of DefaultPlugin.
    """

    @add_customer_response([
        "Let me look up our frame options for you.",
        "I'll find the best frames for your artwork.",
        "Let me check what we have available.",
    ])
    async def search_frame_options(
        self,
        artwork_type: Annotated[str, "Type of artwork (painting, photo, etc.)"],
        style_preference: Annotated[str, "Style preference (modern, traditional, rustic, etc.)"],
        size: Annotated[str | None, "Artwork dimensions if known"] = None,
    ) -> str:
        """
        Search for appropriate frame options based on artwork type and preferences.

        This tool would typically connect to your frame inventory database or catalog.
        For now, it provides helpful recommendations based on common framing practices.
        """
        # TODO: Connect to actual frame inventory database
        # For now, provide general recommendations

        recommendations = []

        # General recommendations based on artwork type
        if "painting" in artwork_type.lower():
            if "oil" in artwork_type.lower():
                recommendations.append(
                    "For oil paintings, we recommend frames with depth to accommodate "
                    "the canvas thickness. Popular options include floating frames or "
                    "gallery-style frames."
                )
            elif "watercolor" in artwork_type.lower():
                recommendations.append(
                    "Watercolors look beautiful with matting to create breathing room. "
                    "We suggest UV-protective glass to prevent fading."
                )

        # Style-based recommendations
        if "modern" in style_preference.lower():
            recommendations.append(
                "For a modern aesthetic, consider our sleek metal frames in silver, "
                "black, or brushed aluminum, or minimalist wood frames with clean lines."
            )
        elif "traditional" in style_preference.lower():
            recommendations.append(
                "Traditional frames work beautifully in ornate wood with gold or "
                "silver leaf accents, or classic walnut and cherry finishes."
            )
        elif "rustic" in style_preference.lower():
            recommendations.append(
                "Rustic frames in reclaimed wood, barnwood finish, or distressed "
                "wood create a warm, lived-in look."
            )

        if not recommendations:
            recommendations.append(
                "We have a wide selection of frames that would work beautifully "
                "for your project. I'd recommend coming in to see samples in person."
            )

        return " ".join(recommendations)

    @add_customer_response([
        "Let me calculate a rough estimate for you.",
        "I can give you a price range for that.",
        "Here's what you can expect cost-wise.",
    ])
    async def estimate_framing_cost(
        self,
        artwork_dimensions: Annotated[str, "Dimensions of the artwork"],
        frame_type: Annotated[
            str, "Type of frame (basic, premium, custom, shadow box, etc.)"
        ],
        glass_type: Annotated[
            str, "Type of glass (regular, UV-protective, museum-quality, none)"
        ],
        include_matting: Annotated[bool, "Whether matting is included"],
    ) -> str:
        """
        Provide a rough cost estimate for a framing project.

        This is a simplified pricing guide. In production, this would connect to
        your actual pricing database or API.
        """
        # TODO: Connect to actual pricing system
        # This is a simplified estimation for demonstration

        # Parse dimensions to get approximate size category
        dimension_str = artwork_dimensions.lower().replace('"', "").replace("inches", "")

        # Simple size categorization
        if any(x in dimension_str for x in ["8x10", "8 x 10", "11x14", "11 x 14"]):
            size_category = "small"
            base_price = 80
        elif any(
            x in dimension_str for x in ["16x20", "16 x 20", "18x24", "18 x 24"]
        ):
            size_category = "medium"
            base_price = 150
        elif any(
            x in dimension_str for x in ["24x36", "24 x 36", "30x40", "30 x 40"]
        ):
            size_category = "large"
            base_price = 250
        else:
            size_category = "custom"
            base_price = 200

        # Adjust for frame type
        if "premium" in frame_type.lower() or "custom" in frame_type.lower():
            base_price *= 1.5
        elif "shadow box" in frame_type.lower():
            base_price *= 1.8

        # Adjust for glass type
        if "uv" in glass_type.lower():
            base_price += 50
        elif "museum" in glass_type.lower():
            base_price += 100

        # Adjust for matting
        if include_matting:
            base_price += 40

        # Create price range
        min_price = int(base_price * 0.85)
        max_price = int(base_price * 1.15)

        return (
            f"For a {size_category} piece ({artwork_dimensions}) with {frame_type} frame, "
            f"{glass_type} glass{' and matting' if include_matting else ''}, "
            f"you're typically looking at ${min_price} to ${max_price}. "
            f"This is a rough estimate - the final quote will depend on the specific "
            f"materials and options you choose. I'll make sure we follow up with a "
            f"detailed written quote."
        )

    @add_customer_response([
        "Let me check our framing guide for that.",
        "I have some information about that in our knowledge base.",
        "Let me look that up for you.",
    ])
    async def get_framing_advice(
        self,
        question: Annotated[
            str, "Customer's question about framing (materials, techniques, care, etc.)"
        ],
    ) -> str:
        """
        Answer common framing questions using the knowledge base.

        This extends the base search_document tool with framing-specific context.
        """
        # Use the parent class's search_document method
        result = await self.search_document(
            search=question,
            # Add framing-specific context to the search
        )

        if not result or "no trainings found" in result.lower():
            return (
                "That's a great question. While I don't have specific details in my "
                "knowledge base right now, I'd be happy to have one of our framing "
                "specialists give you a call to discuss this. They can provide expert "
                "guidance on your specific situation."
            )

        return result

    @add_customer_response([
        "Let me create a reminder for our team.",
        "I'll make a note of that for follow-up.",
        "I've scheduled that for our team to handle.",
    ])
    async def schedule_consultation(
        self,
        customer_name: Annotated[str, "Customer's name"],
        consultation_type: Annotated[
            str,
            "Type of consultation (in-person, phone callback, quote request, etc.)",
        ],
        preferred_timeframe: Annotated[str | None, "When customer prefers"] = None,
    ) -> str:
        """
        Schedule a follow-up consultation for the customer.

        Creates a reminder for the team to follow up with specific consultation requests.
        """
        # Create a reminder using the parent class method
        reminder_text = (
            f"Schedule {consultation_type} for {customer_name}"
            + (f" - prefers {preferred_timeframe}" if preferred_timeframe else "")
        )

        await self.new_or_updated_reminder(
            action_todo=reminder_text,
            description=f"Customer requested {consultation_type}",
        )

        return (
            f"Perfect! I've scheduled a {consultation_type} "
            + (
                f"for {preferred_timeframe}. "
                if preferred_timeframe
                else "for you. "
            )
            + "Our team will reach out to confirm the details."
        )


# Example of how to register custom tools
# In your config or initialization code, you would:
# from app.helpers.llm_tools_jays_frames import JaysFramesPlugin
# plugin = JaysFramesPlugin(call=call)
