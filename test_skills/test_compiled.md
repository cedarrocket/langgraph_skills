# [Node] Research
Please research the user's topic.

## [Transitions]
| Condition | Next Node | Require Approval | Feedback |
| :--- | :--- | :--- | :--- |
| default | Evaluate | no | |

# [Node] Evaluate
Evaluate if the research is complete. If the research is complete, transition to Finish. If the research needs more detail, transition back to Research.

## [Transitions]
| Condition | Next Node | Require Approval | Feedback |
| :--- | :--- | :--- | :--- |
| research is complete | Finish | no | |
| research needs more detail | Research | no | |

# [Node] Finish
- **is_final**: true
Output the final conclusion.