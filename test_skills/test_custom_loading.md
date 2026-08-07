# [Config]
- **max_loops**: 5

You are a custom tool tester.

# [Node] TestTools
- **tools**: custom_search

Please call the custom tool `custom_search` with the query "Vibe Coding".
Once you get the result, submit it as payload to Finish.

## [Transitions]
- Default -> Finish

# [Node] Finish
- **is_final**: true
