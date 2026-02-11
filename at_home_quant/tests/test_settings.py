from at_home_quant.config.settings import Settings
from at_home_quant.data.tickers import BENCHMARKS


def test_default_benchmark_tickers_subset_of_benchmarks() -> None:
    settings = Settings()

    assert set(settings.benchmark_tickers).issubset(BENCHMARKS.keys())
