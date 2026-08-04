You are a master parent agent.

# [State] PreProcess
Tell the system to generate a person's information for a user named Alice who is 30 years old.

## [Transitions]
- Default -> GenerateInfoChild

# [State] GenerateInfoChild
- **type**: skill
- **src**: test_skills/test_json_skill.md

## [Transitions]
- Default -> ReportResult

# [State] ReportResult
- **is_final**: true

Display the final JSON payload returned from the child skill.
