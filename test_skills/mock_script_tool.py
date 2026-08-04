import sys

def main():
    # Read the input payload from standard input
    input_data = sys.stdin.read().strip()
    print(f"MOCK_SCRIPT_OUTPUT: Recieved '{input_data}' and returning the processed message.")

if __name__ == "__main__":
    main()
