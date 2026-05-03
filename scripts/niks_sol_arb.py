"""
niks_sol_arb.py  —  SOL/USDC arbitrage scanner + paper-trade executor

Runs NiksSolArbController to scan 30 exchanges and paper-trade on crypto_com_paper_trade.

Start in hummingbot:
  start --script niks_sol_arb.py --conf conf_niks_sol_arb.yml
"""
import os
from decimal import Decimal
from typing import Dict, List

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction


class NiksSolArbScriptConfig(StrategyV2ConfigBase):
    script_file_name: str = os.path.basename(__file__)
    controllers_config: List[str] = ["conf_niks_sol_arb_controller.yml"]


class NiksSolArbScript(StrategyV2Base):
    """
    Thin wrapper — all logic lives in NiksSolArbController.
    This script just runs the controller and reports status.
    """

    def __init__(self, connectors: Dict[str, ConnectorBase], config: NiksSolArbScriptConfig):
        super().__init__(connectors, config)

    def create_actions_proposal(self) -> List[CreateExecutorAction]:
        return []

    def stop_actions_proposal(self) -> List[StopExecutorAction]:
        return []
