import os
import sys
import subprocess

def speak(text):
    print(f"Narrating: {text}")
    # Using the high-quality 'Evan' voice built into macOS
    # -v Evan sets the voice, -r 150 slows it down slightly for a Bob Ross feel
    try:
        subprocess.run(['say', '-v', 'Evan', '-r', '160', text])
    except Exception as e:
        print(f"Error playing voice: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        speak(sys.argv[1])
    else:
        speak("Ready to paint some happy little trees.")
