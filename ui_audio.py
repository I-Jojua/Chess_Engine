"""Optional, generated UI sound effects with graceful no-audio fallback."""

from __future__ import annotations

from array import array
import math


class SoundManager:
    def __init__(self, pygame_module) -> None:
        self.pygame = pygame_module
        self.available = False
        self.sounds = {}
        try:
            pygame_module.mixer.init(frequency=22050, size=-16, channels=1)
            self.sounds = {name: self._tone(freq) for name, freq in {
                "move": 520, "capture": 300, "check": 740, "result": 440,
            }.items()}
            self.available = True
        except Exception:
            self.available = False

    def _tone(self, frequency: int):
        sample_rate = 22050
        samples = array("h")
        for index in range(int(sample_rate * 0.075)):
            envelope = 1.0 - index / (sample_rate * 0.075)
            value = int(5000 * envelope * math.sin(2 * math.pi * frequency * index / sample_rate))
            samples.append(value)
        return self.pygame.mixer.Sound(buffer=samples.tobytes())

    def play(self, name: str, enabled: bool = True) -> None:
        if enabled and self.available and name in self.sounds:
            self.sounds[name].play()
