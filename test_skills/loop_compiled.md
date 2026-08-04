# [State] Draft
- **type**: llm

Write or update the article draft based on the user's topic and any feedback received from the Edit phase.

## [Transitions]
- Default -> Edit

# [State] Edit
- **type**: llm

Evaluate the current draft. 
If this is the first iteration, you MUST reject the draft, provide feedback that "it needs to mention the NASA Artemis mission", and transition back to Draft.
If this is the second iteration (and it mentions the Artemis mission), approve it and transition to Publish.

Valid next states are: Draft, Publish.

## [Transitions]
| Condition | Next State | Require Approval | Feedback |
| :--- | :--- | :--- | :--- |
| need_revision | Draft | no | it needs to mention the NASA Artemis mission |
| approved | Publish | no | |

# [State] Publish
- **is_final**: true
- **type**: llm

Output the final, polished article and state that it has been published.