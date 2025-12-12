import random
import math
import time
from collections import deque
from tower import Tower
from ue import UE

# Notes:
#       - We need to define how large an ethernet frame is in bytes (1518 bytes max?)


def run_env_main(t_delta, verbose=False):
    # Need some dynamic allocation of IP addresses. For now just use the for loop iterator.
    # We can say that the first 50 IP addresses are for towers and the rest are for UEs.
    towers = [Tower(i, random.uniform(0, 1500), random.uniform(-100, 100), t_delta=t_delta, ip_addr=i, verbose=verbose) for i in range(3)]  # 3 towers in a line
    towers[0].x_pos = 0
    towers[0].y_pos = 0
    # towers[1].x_pos = 1000000
    # towers[1].y_pos = 1000000
    # towers[2].x_pos = 1000000
    # towers[2].y_pos = 1000000  
    ues = [UE(i, random.uniform(0, 1500), random.uniform(-100, 100), towers, t_delta=t_delta, ip_addr=i+50, verbose=verbose) for i in range(5)]
    for ue in ues:
        ue.x_pos = 1000
        ue.y_pos = 1000
    # ues[0].x_pos = 0
    # ues[0].y_pos = 0

    # *** RUN THIS LOOP EVERY TIME A NEW TOWER IS ADDED ***
    # This is to ensure the network doesn't flood in a ring topology
    # (WE ARE BROADCASTING EVERY MESSAGE)
    n_towers = len(towers)
    for tower in towers:
        tower.tx_attempts = n_towers

    t_count = 0
    t_step  = 0
    src_ue  = random.randint(0, 4)
    dest_ip = random.randint(50, 54)

    # Connect the three towers in an arbitrary way
    # Testing ring topology
    towers[0].connect_tower(towers[1])
    towers[2].connect_tower(towers[1])
    # towers[2].connect_tower(towers[0])

    while 1:
        # Make sure all timesteps are the same
        for ue in ues:
            ue.t_step = t_step

        # if t_count % 10 == 0: # wait to tx data every 10 timeouts (5 seconds in our case)
            # while (random.random() < 0.25): # 75% chance of sending more data
        # Example 0: Test transmitting only a single UE at a time
        # ues[src_ue].set_tx_bytes(n_bytes=random.randint(1, 1518), dest_ip=dest_ip)
        # src_ue  = random.randint(0, 4)
        # Example 1: Test transmitting from all UEs at once
        for ue in ues:
            ue.set_tx_bytes(n_bytes=random.randint(1, 1518), dest_ip=dest_ip)
            # ue.set_tx_bytes(n_bytes=int(ue.max_data_rate - 1), dest_ip=dest_ip)
            dest_ip = random.randint(50, 54)

        # Step through the calculations
        for ue in ues:
            ue.step()

        can_tx = True 
        tx_count = 0
        while can_tx:
            can_tx = False
            for tower in towers:
                if tower.can_transmit():
                    can_tx = True
                    tower.step()
                    tx_count += 1

        # Example 2: Printing data rates
        # Print actual data rate and max data rate of each device
        for tower in towers:
            print(f"Tower IP_ADDR {tower.ip_addr}: Data rate = {tower.n_tx_bytes * 8 * 1e-6} Mbps, Max data rate = {tower.max_data_rate * 1e-6} Mbps")

        for ue in ues:
            if ue.current_tower is not None:
                print(f"UE IP_ADDR {ue.ip_addr}: Tower IP_ADDR = {ue.current_tower.ip_addr} Band = {ue.freq_band}, Code rate = {ue.code_rate}, Data rate = {ue.n_tx_bytes * 8 * 1e-6} Mbps, Max data rate = {ue.max_data_rate * 1e-6} Mbps")

        # Clear the transmission counter per timestep
        # Clearning for towers is NECESSARY!
        # this is so that we can run the while can_tx loop 
        # until the towers reach the max data rate
        for tower in towers:
            tower.clear_tx_count()
        for ue in ues:
            ue.clear_tx_count()

        # Test tower shut-downs
        # EXAMPLE 3: Start by disabling the first UEs tower
        # if t_step % 20 == 10:
            # print(f"Tower IP_ADDR {ues[0].current_tower.ip_addr}: Simulating Outage")
            # disabled_twr = ues[0].current_tower
            # if disabled_twr is not None:
                # towers.remove(disabled_twr)
                # for ue in ues:
                    # ue.update_towers(towers)
        # elif t_step % 20 == 19:
            # print(f"Tower IP_ADDR {ues[0].current_tower.ip_addr}: Recovered Functionality")
            # towers.append(disabled_twr)
            # for ue in ues:
                # ue.update_towers(towers)

        # EXAMPLE 4: Disable all towers temporarily and see if UEs can buffer data
        #           without dropping any packets
        # if t_step % 10 == 7:
            # print(f"ALL Towers: Simulating Outage")
            # if towers is not None:
                # for ue in ues:
                    # ue.update_towers([])
        # elif t_step % 10 == 9:
            # for ue in ues:
                # ue.update_towers(towers)
            # print(f"ALL Towers: Recovered Functionality")


        # Example 5: Move UE 0
        # for ue in ues:
            # ue.move()
            # print(f"UE IP_ADDR {ue.ip_addr} Position: x = {ue.x_pos}, y = {ue.y_pos}")

        time.sleep(t_delta)
        print(f"Timestep {t_step}: Completed.")
        t_step += 1

if __name__ == "__main__":
    # Create the simpy environment
    factor            = 2.0
    steps_per_timeout = 1.0
    t_delta           = steps_per_timeout / factor
    
    # Was running simpy before, but system sleeps is better

    run_env_main(t_delta)
