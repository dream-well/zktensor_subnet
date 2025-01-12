import os
import time
import torch
import argparse
import traceback
import bittensor as bt
import json
import protocol

import random
from execution_layer.ZkSqrtModelSession import ZkSqrtModelSession

from utils import try_update

class MinerSession:
    def __init__(self, config):
        self.config = config
        self.configure()
        self.check_register()
        
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        None
        return False
    
    def unpack_bt_objects(self):
        wallet = self.wallet
        metagraph = self.metagraph
        subtensor = self.subtensor
        return wallet, metagraph, subtensor

    def start_axon(self):
        wallet, metagraph, subtensor = self.unpack_bt_objects()

        axon = bt.axon( wallet = wallet, config = self.config )
        bt.logging.info(f"Axon {axon}")

        # Attach determiners which functions are called when servicing a request.
        bt.logging.info(f"Attaching forward function to axon.")
        axon.attach(forward_fn = self.queryZkProof)

        # Serve passes the axon information to the network + netuid we are hosting on.
        # This will auto-update if the axon port of external ip have changed.
        bt.logging.info(f"Serving axon {self.queryZkProof} on network: {self.config.subtensor.chain_endpoint} with netuid: {self.config.netuid}")
        axon.serve( netuid = self.config.netuid, subtensor = subtensor )

        # Start  starts the miner's axon, making it active on the network.
        bt.logging.info(f"Starting axon server on port: {self.config.axon.port}")
        axon.start()

        self.axon = axon
        
    def run(self):
        """Keep the miner alive. his loop maintains the miner's operations until intentionally stopped.
        
        """

        bt.logging.info(f"Starting main loop")
        wallet, metagraph, subtensor = self.unpack_bt_objects()
        
        self.start_axon()
        
        step = 0
        last_updated_block = subtensor.block - 100
        
        while True:
            if step % 10 == 0 and self.config.auto_update == True:
                try_update()          
            try:
                if subtensor.block - last_updated_block >= 100:
                    bt.logging.trace(f"Setting miner weight")
                    # find the uid that matches config.wallet.hotkey [meta.axons[N].hotkey == config.wallet.hotkey]
                    # set the weight of that uid to 1.0
                    uid = None
                    try:
                        for _uid, axon in enumerate(metagraph.axons):
                            if axon.hotkey == wallet.hotkey.ss58_address:
                                uid = _uid
                                break
                    except Exception as e:
                        bt.logging.warning(f"Could not set miner weight: {e}")
                        raise e
                # Below: Periodically update our knowledge of the network graph.
                if step % 5 == 0:
                    metagraph = subtensor.metagraph(self.config.netuid)
                    log =  (f'Step:{step} | '\
                            f'Block:{metagraph.block.item()} | '\
                            f'Stake:{metagraph.S[self.subnet_uid]} | '\
                            f'Rank:{metagraph.R[self.subnet_uid]} | '\
                            f'Trust:{metagraph.T[self.subnet_uid]} | '\
                            f'Consensus:{metagraph.C[self.subnet_uid] } | '\
                            f'Incentive:{metagraph.I[self.subnet_uid]} | '\
                            f'Emission:{metagraph.E[self.subnet_uid]}')
                    bt.logging.info(log)
                step += 1
                time.sleep(1)


            # If someone intentionally stops the miner, it'll safely terminate operations.
            except KeyboardInterrupt:
                bt.logging.success('Miner killed by keyboard interrupt.')
                break
            # In case of unforeseen errors, the miner will log the error and continue operations.
            except Exception as e:
                bt.logging.error(traceback.format_exc())
                continue

    def check_register(self):
        if self.wallet.hotkey.ss58_address not in self.metagraph.hotkeys:
            bt.logging.error(f"\nYour miner: {self.wallet} if not registered to chain connection: {self.subtensor} \nRun btcli register and try again.")
            exit()
        else:
            # Each miner gets a unique identity (UID) in the network for differentiation.
            subnet_uid = self.metagraph.hotkeys.index(self.wallet.hotkey.ss58_address)
            bt.logging.info(f"Running miner on uid: {subnet_uid}")
            self.subnet_uid = subnet_uid
        
    def configure(self):
        # === Configure Bittensor objects ====
        self.wallet = bt.wallet( config = self.config )
        self.subtensor = bt.subtensor( config = self.config )
        self.metagraph = self.subtensor.metagraph( self.config.netuid )
        self.sync_metagraph()
        
    def sync_metagraph(self):
        self.metagraph.sync(subtensor = self.subtensor)
    
    def queryZkProof(self,  synapse: protocol.QueryZkProof) -> protocol.QueryZkProof: 
        """
        This function run proof generation of the model (with its output as well)
        """
        bt.logging.debug(f"Received request from validator QueryZkProof")
        bt.logging.info(f"required data: {synapse.query_input} \n")
        if synapse.query_input is not None:
            model_id = synapse.query_input["model_id"]
            public_inputs = synapse.query_input["public_inputs"]
        else:
            # search_key = [random_line()]
            bt.logging.info(f"picking random model_id and public_inputs: \n")
        
        # Fetch latest N posts from miner's local database.
        try:
            model_session = ZkSqrtModelSession(public_inputs)
            bt.logging.debug(f"ModelSession created succesfully")
            synapse.query_output = model_session.gen_proof()
            model_session.end()
        except Exception as e:
            synapse.query_output = "An error occured"
            
            bt.logging.error(f"error", e)

        
        bt.logging.info(f"Proof generation success \n")
        return synapse