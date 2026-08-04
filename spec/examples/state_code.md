# [State] Check
- **type**: code

```python
guess_payload = get_payload()
try:
    guess = int(guess_payload)
except (ValueError, TypeError):
    guess = 50

if guess == 42:
    transition_to('Win', 'Correct')
else:
    transition_to('Guess', 'Too high' if guess > 42 else 'Too low')
```

## [Transitions]
- Default -> Win

# [State] Win
- **is_final**: true
