# [State] Research
Please research the user's topic.

## [Transitions]
| Condition | Next State | Require Approval | Feedback |
| :--- | :--- | :--- | :--- |
| default | Evaluate | no | |

# [State] Evaluate
Evaluate if the research is complete. If the research is complete, transition to Finish. If the research needs more detail, transition back to Research.

## [Transitions]
| Condition | Next State | Require Approval | Feedback |
| :--- | :--- | :--- | :--- |
| research is complete | Finish | no | |
| research needs more detail | Research | no | |

# [State] Finish
- **is_final**: true
Output the final conclusion.