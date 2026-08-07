You are a master parent agent.

# [Node] PreProcess
Tell the system to generate a person's information for a user named Alice who is 30 years old.

## [Transitions]
- Default -> GenerateInfoChild

# [Node] GenerateInfoChild
- **type**: skill
- **src**: test_skills/test_json_skill.md

## [Transitions]
- Default -> ReportResult

# [Node] ReportResult
- **is_final**: true

Display the final JSON payload returned from the child skill.
