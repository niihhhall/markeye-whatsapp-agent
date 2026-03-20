import sys
import os

# Add the project root to sys.path
sys.path.append(os.getcwd())

from app.calendly import extract_phone_from_payload, normalize_phone

def test_extraction():
    # Mock Calendly payload
    payload = {
        "questions_and_answers": [
            {
                "question": "What is your WhatsApp number?",
                "answer": " +91 8160178327 "
            },
            {
                "question": "Name",
                "answer": "Nihal Mishra"
            }
        ]
    }
    
    extracted = extract_phone_from_payload(payload)
    print(f"Extracted: '{extracted}'")
    
    if extracted:
        normalized = normalize_phone(extracted)
        print(f"Normalized: '{normalized}'")
        
        expected = "whatsapp:+918160178327"
        if normalized == expected:
            print("✅ Extraction and Normalization Success!")
        else:
            print(f"❌ Normalization Failed. Expected {expected}, got {normalized}")
    else:
        print("❌ Extraction Failed.")

def test_alternate_keywords():
    payloads = [
        {"questions_and_answers": [{"question": "Mobile", "answer": "07700900000"}]},
        {"questions_and_answers": [{"question": "phone number", "answer": "+447700900000"}]}
    ]
    
    for p in payloads:
        extracted = extract_phone_from_payload(p)
        print(f"Testing '{p['questions_and_answers'][0]['question']}': Extracted '{extracted}' -> Normalized '{normalize_phone(extracted)}'")

if __name__ == "__main__":
    test_extraction()
    print("-" * 20)
    test_alternate_keywords()
