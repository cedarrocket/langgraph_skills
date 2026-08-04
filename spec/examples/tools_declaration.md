# [Tools]
- **mock_tool**:
  - **type**: script
  - **src**: test_skills/mock_script_tool.py
  - **description**: A mock script tool that processes the given string payload.
- **api_tool**:
  - **type**: api
  - **url**: http://api.example.com/data
  - **method**: POST
  - **description**: A mock API tool.

You are a tool testing agent.

# [State] TestTool
- **tools**: mock_tool, api_tool

Your task is to call the registered tools and summarize the results.

## [Transitions]
- Default -> Finish

# [State] Finish
- **is_final**: true

Display the final output.
