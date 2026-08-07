You are a JSON generator helper.

# [Node] GenerateJSON
Generate a JSON object representing a person's information.
It must contain:
1. `name` (string)
2. `age` (integer, must be greater than or equal to 18)

IMPORTANT: To test the self-healing of JSON validation, please intentionally output an invalid format first (e.g. age = 10, or invalid JSON syntax) on your very first turn. Once the environment returns a JSON validation error, output the correct compliant JSON in the subsequent turn.

## [Output JSON]
```json
{
  "type": "object",
  "properties": {
    "name": { "type": "string" },
    "age": { "type": "integer", "minimum": 18 }
  },
  "required": ["name", "age"]
}
```

## [Transitions]
- Default -> Finish

# [Node] Finish
- **is_final**: true

Display the final valid JSON and stop.
