from typing import Union

def add_numbers(a: Union[int, float], b: Union[int, float]) -> Union[int, float]:
    return a + b

def greet(name: str) -> str:
    return f"Hello, {name}"

class Calculator:
    def __init__(self, brand: str) -> None:
        self.brand = brand

    def multiply(self, x: Union[int, float], y: Union[int, float]) -> Union[int, float]:
        return x * y