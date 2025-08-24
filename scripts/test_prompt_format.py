#!/usr/bin/env python3
"""
Test script to compare how well the 8B vs 3B models follow the new prompt format.
Tests the 3 key formatting requirements:
1. "Here's what we found:" greeting + bullet points with URLs
2. "A few thoughts:" section with explanations  
3. Follow-up questions at the end
"""

import os
import sys
import django
import asyncio
from datetime import datetime

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from api.llm_service import get_llm_service, create_event_discovery_prompt
from api.rag_service import get_rag_service

async def test_prompt_format():
    """Test both models with sample queries to check format compliance."""
    
    llm_service = get_llm_service()
    rag_service = get_rag_service()
    
    # Sample test queries
    test_queries = [
        "I have a 3 and 5 year old, I'm in Newton, MA and need something to do this weekend",
        "Looking for indoor activities for adults near Salem this evening",
        "Family-friendly events with kids under 10 in the Boston area"
    ]
    
    print("🧪 Testing Prompt Format Compliance: 8B vs 3B Models")
    print("="*70)
    
    for i, query in enumerate(test_queries, 1):
        print(f"\n📝 Test Query {i}: {query}")
        print("-" * 60)
        
        # Get relevant events for context
        try:
            context_events = rag_service.get_context_events(
                user_message=query,
                max_events=5,
                similarity_threshold=0.2
            )
        except Exception as e:
            print(f"⚠️  RAG error: {e}, using fallback events")
            context_events = [
                {
                    'id': 1,
                    'title': 'Family Fun Day at Library',
                    'description': 'Activities for all ages including crafts and stories',
                    'location': 'Newton Library',
                    'start_time': datetime.now().isoformat(),
                    'end_time': None,
                    'url': 'https://example.com/family-fun'
                }
            ]
        
        # Create prompts
        system_prompt, user_prompt = create_event_discovery_prompt(
            query, context_events, {
                'current_date': datetime.now().isoformat(),
                'location': None,
                'preferences': {}
            }
        )
        
        # Test both models
        models_to_test = [
            ("llama3.1:8b", "🚀 Model A (8B)"),
            ("llama3.2:3b", "⚡ Model B (3B)")
        ]
        
        for model_name, display_name in models_to_test:
            print(f"\n{display_name}:")
            
            try:
                # Generate response
                response = await llm_service.generate_response(
                    model=model_name,
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                    timeout_seconds=45
                )
                
                if response.success:
                    print(f"⏱️  Response time: {response.response_time_ms}ms")
                    print(f"📄 Response:\n{response.response}\n")
                    
                    # Analyze format compliance
                    analyze_format_compliance(response.response, display_name)
                else:
                    print(f"❌ Error: {response.error}")
                    
            except Exception as e:
                print(f"❌ Exception: {e}")
        
        print("\n" + "="*70)

def analyze_format_compliance(response: str, model_name: str):
    """Analyze how well the response follows the required format."""
    
    print(f"📊 Format Analysis for {model_name}:")
    
    # Check 1: Greeting + bullet points
    has_greeting = any(phrase in response.lower() for phrase in [
        "here's what we found",
        "here's what i found", 
        "what we found",
        "what i found"
    ])
    
    has_bullets = "•" in response or "*" in response
    
    # Check 2: "A few thoughts" section
    has_thoughts_section = any(phrase in response.lower() for phrase in [
        "a few thoughts",
        "some thoughts",
        "thoughts:",
        "here are some thoughts"
    ])
    
    # Check 3: Follow-up question
    has_question = "?" in response
    question_at_end = response.strip().endswith("?")
    
    # Check 4: URL inclusion
    has_urls = any(indicator in response for indicator in ["http", "[", "www", ".com"])
    
    # Results
    checks = [
        ("✅ Greeting present" if has_greeting else "❌ Missing greeting", has_greeting),
        ("✅ Bullet points used" if has_bullets else "❌ No bullet points", has_bullets),
        ("✅ 'Thoughts' section" if has_thoughts_section else "❌ No thoughts section", has_thoughts_section),
        ("✅ Has question" if has_question else "❌ No questions", has_question),
        ("✅ Question at end" if question_at_end else "❌ Question not at end", question_at_end),
        ("✅ URLs included" if has_urls else "❌ No URLs found", has_urls)
    ]
    
    for check_desc, passed in checks:
        print(f"  {check_desc}")
    
    # Overall score
    score = sum(1 for _, passed in checks if passed)
    total = len(checks)
    print(f"  🎯 Format Score: {score}/{total} ({score/total*100:.0f}%)")

if __name__ == "__main__":
    asyncio.run(test_prompt_format())
