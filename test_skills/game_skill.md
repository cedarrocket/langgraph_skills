# [Config]
- **max_loops**: 8

You are a number guessing agent. The target number is 42.

# [Node] Guess
Make a new numeric guess based on the feedback from the previous Check phase. 
Initially (if there is no feedback), guess 50.
Do not guess 42 on the first try so we can test the loop.
If the feedback says "Too high", you must guess a LOWER number.
If the feedback says "Too low", you must guess a HIGHER number.

## [Transitions]
- Default -> Check

# [Node] Check
- **type**: code

```python
# Using the injected SDK functions to read payloads and transit states
guess_payload = get_payload()
try:
    guess = int(guess_payload)
except (ValueError, TypeError):
    guess = 50

if guess == 42:
    transition_to('Win', 'Correct')
elif guess > 42:
    transition_to('Guess', 'Too high')
else:
    transition_to('Guess', 'Too low')
```

## [Transitions]
- Correct -> Win
- Too high -> Guess
- Too low -> Guess

# [Node] Win
- **is_final**: true

State that the target number 42 has been guessed successfully!
