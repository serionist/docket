"""The gate's shard plugin, loaded here rather than with `-p`, so it is imported once coverage runs. A plugin `-p` names
is imported before pytest-cov starts, and the lines its import runs would read as never run in CI's covered shares,
though they run in `gate full` too, which runs the full lane as its one share, 1/1 (DR-0754). Without --shard or
--select the plugin does nothing."""

pytest_plugins = ["devkit.shard"]
