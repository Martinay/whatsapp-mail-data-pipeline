"""Interactive agent with function-calling for querying messages."""

import json
import os

from dotenv import load_dotenv
from openai import OpenAI

from prompts import SYSTEM_PROMPT
from tools import TOOL_DEFINITIONS, TOOL_MAP

load_dotenv()


def _init_client() -> OpenAI:
    api_key = os.getenv("MISTRAL_API_KEY_TRANSCRIBE")
    base_url = os.getenv("MISTRAL_BASE_URL")
    if not api_key or not base_url:
        raise ValueError("MISTRAL_API_KEY_TRANSCRIBE and MISTRAL_BASE_URL must be set")
    return OpenAI(api_key=api_key, base_url=base_url)


def run_agent(client: OpenAI, messages: list[dict]) -> str:
    """Run one agent turn: send messages, handle tool calls, return final response."""
    while True:
        response = client.chat.completions.create(
            model="mistral-medium-latest",
            messages=messages,
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
        )

        choice = response.choices[0]

        # No tool calls → final response
        if not choice.message.tool_calls:
            return choice.message.content or ""

        # Process tool calls
        messages.append(choice.message)

        for tool_call in choice.message.tool_calls:
            fn_name = tool_call.function.name
            fn_args = json.loads(tool_call.function.arguments)

            print(f"  🔧 {fn_name}({json.dumps(fn_args, ensure_ascii=False)[:200]})")

            fn = TOOL_MAP.get(fn_name)
            if fn is None:
                result = {"error": f"Unknown tool: {fn_name}"}
            else:
                try:
                    result = fn(**fn_args)
                except Exception as e:
                    result = {"error": str(e)}

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )


def main():
    client = _init_client()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    print("Agent bereit. Frage eingeben (oder 'exit'):\n")

    while True:
        try:
            user_input = input("Du: ").strip().replace("\r", "")
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input or user_input.lower() in ("exit", "quit"):
            break

        messages.append({"role": "user", "content": user_input})
        response = run_agent(client, messages)
        messages.append({"role": "assistant", "content": response})

        print(f"\nAssistent: {response}\n")


if __name__ == "__main__":
    main()
