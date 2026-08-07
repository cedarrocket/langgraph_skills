#!/usr/bin/env lgskills

You are a tech research assistant. Your task is to search the web for a given tech topic, compile findings, and output a formatted Markdown report.

# [Config]
- **max_loops**: 40

# [IO]
- **reader**: txt_reader
- **writer**: txt_writer

# [Node] SearchInfo
- **type**: llm
- **tools**: web_search

Search the web for the tech topic provided in the payload.

## [Transitions]
- Default -> CompileReport

# [Node] CompileReport
- **type**: llm
- **max_loops**: 8

Compile findings into a validated JSON structure.

## [Output JSON]
```json
{
  "type": "object",
  "properties": {
    "topic": { "type": "string" },
    "findings": { "type": "array", "items": { "type": "string" } }
  },
  "required": ["topic", "findings"]
}
```

## [Transitions]
| Condition | Next Node | Require Approval | Feedback |
| :--- | :--- | :--- | :--- |
| topic unclear | SearchInfo | no | Clarify the topic. |
| findings complete | FormatReport | yes | |

# [Node] FormatReport
- **is_final**: true

Display the final report.
