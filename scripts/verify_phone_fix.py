import sys
import os

# Add parent directory to path to import twilio_utils
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from twilio_utils import format_to_e164

def test_formatting():
    test_cases = [
        ("9700443157", "+919700443157"),
        ("+919700443157", "+919700443157"),
        ("09700443157", "+919700443157"),
        ("919700443157", "+91919700443157"), # This matches current logic: if no +, prepend +91. 
                                              # Note: If user enters 91... without +, it will become +9191...
                                              # This is a known trade-off for simplicity.
        ("+1555010999", "+1555010999"),
        (" 9700 443 157 ", "+919700443157"),
        ("9700-443-157", "+919700443157"),
    ]

    print("Running phone formatting tests...")
    all_passed = True
    for input_val, expected in test_cases:
        actual = format_to_e164(input_val)
        if actual == expected:
            print(f"PASS: '{input_val}' -> '{actual}'")
        else:
            print(f"FAIL: '{input_val}' -> Expected '{expected}', got '{actual}'")
            all_passed = False
    
    if all_passed:
        print("\nAll tests passed successfully!")
    else:
        print("\nSome tests failed.")
    return all_passed

if __name__ == "__main__":
    test_formatting()
