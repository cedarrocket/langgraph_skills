# [Tools]
- **mock_tool**:
  - **type**: script
  - **src**: test_skills/mock_script_tool.py
  - **description**: A mock script tool that processes the given string payload and returns a success output.

You are a tool testing agent.

# [Node] TestTool
- **tools**: mock_tool

Your task is to:
1. Call the registered tool `mock_tool` with the argument payload "Testing dynamic script tools!".
2. Once you receive the response from the tool, display it and summarize that the execution worked.

## [Transitions]
- Default -> Finish

# [Node] Finish
- **is_final**: true

Display the final output and stop.
