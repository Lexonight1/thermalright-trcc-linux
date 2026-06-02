"""UIs — thin adapters over the Command API.

Each UI is independent: CLI, GUI, API.  They all build Commands and
dispatch them through App.  No shared ABC — Commands are the contract.
"""
