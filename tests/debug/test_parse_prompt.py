"""Test parse_chat_prompt with list 17's exact prompt."""
import sys
sys.path.insert(0, '/app')

from app.api.chat_prompt import parse_chat_prompt
import json

prompt = "I want cozy feel good movies like the hangover prefer stuff after 2000, comedies with a bit of action"
filters = parse_chat_prompt(prompt)
print("Parsed filters:")
print(json.dumps(filters, indent=2))
