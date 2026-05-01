#!/usr/bin/env python3
"""
Package tracker - main entry point.
Delegates to CLI implementation while keeping backward compatibility.
"""
from pkg_tracker.cli import main

if __name__ == "__main__":
    main()
