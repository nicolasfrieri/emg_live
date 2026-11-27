import unittest

import numpy as np

from analizar_emg import compute_scalogram


class WaveletAnalysisTests(unittest.TestCase):
    def test_scalogram_resolves_tone_across_short_sample_loss(self):
        sample_rate = 1000
        times = np.arange(12 * sample_rate) / sample_rate
        signal = np.sin(2 * np.pi * 100 * times)
        missing = np.zeros(len(times), dtype=int)
        missing[6 * sample_rate] = 1
        times[6 * sample_rate:] += 1 / sample_rate

        frequencies, runs, _ = compute_scalogram(
            times, signal, missing, sample_rate, low_hz=30, high_hz=300)

        self.assertEqual(len(runs), 1)
        mean_power = runs[0][1].mean(axis=1)
        dominant = frequencies[np.argmax(mean_power)]
        self.assertAlmostEqual(dominant, 100, delta=15)

    def test_scalogram_splits_at_long_recording_gap(self):
        sample_rate = 1000
        times = np.arange(12 * sample_rate) / sample_rate
        times[6 * sample_rate:] += 1
        signal = np.sin(2 * np.pi * 100 * times)
        missing = np.zeros(len(times), dtype=int)
        missing[6 * sample_rate] = sample_rate

        _, runs, _ = compute_scalogram(
            times, signal, missing, sample_rate, low_hz=30, high_hz=300)

        self.assertEqual(len(runs), 2)
        self.assertLess(runs[0][0][-1], runs[1][0][0])


if __name__ == "__main__":
    unittest.main()
