#!/usr/bin/env python3
"""Test parsing of List 17's exact prompt."""

import sys
import os
sys.path.insert(0, '/app')

from app.api.chat_prompt import parse_chat_prompt, generate_dynamic_title
import json

prompt = "I want cozy feel good movies like the hangover prefer stuff after 2000, comedies with a bit of action"

print(f"Testing prompt: {prompt}\n")
result = parse_chat_prompt(prompt)
print("Parsed filters:")
print(json.dumps(result, indent=2))

# Generate dynamic title
dynamic_title = generate_dynamic_title(result, prompt)
print(f"\nGenerated title: {dynamic_title}")

# Check what should have been extracted
print("\n=== Analysis ===")
print(f"✓ Contains 'movies': {('movie' in result.get('media_types', []))}")
print(f"✓ Contains 'comedies': {('Comedy' in result.get('genres', []))}")
print(f"✓ Contains 'action': {('Action' in result.get('genres', []))}")
print(f"✓ Contains 'cozy' mood: {any('cozy' in m for m in result.get('mood', []))}")
print(f"✓ Contains 'feel-good' mood: {any('feel' in m for m in result.get('mood', []))}")
print(f"✓ Contains 'year_from 2000': {(result.get('year_from') == 2000)}")
print(f"✓ Contains anchor 'the hangover': {('hangover' in result.get('similar_to_title', '').lower())}")

# Test additional prompts
print("\n\n=== Additional Test Cases ===\n")

test_cases = [
    "Show me scary horror movies from the 90s",
    "I need some romantic comedies, preferably romcoms",
    "Give me intense thrillers with suspense",
    "Feel-good family movies for kids",
]

for test_prompt in test_cases:
    print(f"Prompt: {test_prompt}")
    test_result = parse_chat_prompt(test_prompt)
    print(f"  Genres: {test_result.get('genres', [])}")
    print(f"  Moods: {test_result.get('mood', [])}")
    print(f"  Media: {test_result.get('media_types', [])}")
    print(f"  Title: {generate_dynamic_title(test_result, test_prompt)}")
    print()
