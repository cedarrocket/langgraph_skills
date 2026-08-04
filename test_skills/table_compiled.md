# [State] Draft
- **type**: llm

Write an article about AI.

## [Transitions]
- Default -> Edit

# [State] Edit
- **type**: llm

Review the article.

## [Transitions]
| Condition | Next State | Require Approval | Feedback |
| :--- | :--- | :--- | :--- |
| Too short | Draft | no | The article is too short. Please expand. |
| Grammatical errors | Draft | no | Fix grammatical errors. |
| Acceptable | Publish | no | |

# [State] Publish
- **is_final**: true
- **type**: llm

Publish the article.