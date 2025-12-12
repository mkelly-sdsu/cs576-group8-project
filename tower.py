import random
import math
from collections import deque

#Parameters
# High band (mmWave)
HIGH_BAND_RANGE = 300 #meters
HIGH_BAND_THROUGHPUT = 1e9 #1Gbps
# Mid band (sub-6 GHz)
MID_BAND_RANGE = 1500 #meters
MID_BAND_THROUGHPUT = 200e6 #200Mbps
# Low band (sub-1 GHz)
LOW_BAND_RANGE = 5000 #meters
LOW_BAND_THROUGHPUT = 50e6 #50Mbps

#Tower Class
#TODO Log statistics
class Tower:
    def __init__(self, tower_id, x_pos, y_pos, outage_prob=0.01, outage_duration=5, t_delta=None, ip_addr=None, verbose=False):
        # self.env = env
        self.tower_id = tower_id # tower identifier
        self.x_pos = x_pos # x position of tower
        self.y_pos = y_pos # y position of tower
        self.connected_ues = [] #list of connected UEs
        self.connected_towers = [] # list of connected towers (we set the connections)
        self.operational = True # Tower starts as operational
        self.outage_prob = outage_prob #probablity per step that this tower goes down
        self.outage_duration = outage_duration # duration in secs in case of outage
        self.n_ues = 0 # Keep track of the number of connected UEs to update Data Rate
        # self.env.process(self.simulate_outage())

        # new variables
        assert t_delta is not None
        self.t_delta = t_delta
        self.max_data_rate = 10e9 # in bits-per-second per connected device (10G)
        self.n_tx_bytes = 0
        self.n_rx_bytes = 0
        assert ip_addr is not None
        self.ip_addr     = ip_addr # can also take tower_id
        self.buffer      = deque([]) # Deque of how many bytes and where they are going to.
        self.buff_thresh = 10e9 # Max internal buffer size (in bits). Let it be of size 10Gb for now.
                                # If tower is maxed out, do not accept any more data (UE needs to retransmit)
        self.tx_attempts = 50 # Assume 50 towers MAX within the Freq range. We only want to broadcast a 
                              # message a max of 50 times. This will reduce network congestion. This is 
                              # since broadcasting messages will flood the network in a ring topology
        self.ber = 0. # Again, bit-error rate counter per timestep
        self.total_bit_tx = 1 # cumulative bit transmission count
        self.bit_errors = 0 # cumulative bit-errors

        # Store the current data rate of all UEs
        self.ue_rates = {}
        # Store the overall tx bits to each of the UEs
        self.ue_tx_bits = {}

        # Number of UEs in each band
        self.n_bands = {
            "high" : 0,
            "mid"  : 0,
            "low"  : 0
        }

        self.verbose = verbose
        self.broadcast_ip = 65535

    # Determines the data rate for a given UE based on its distance from the tower
    def set_data_rate(self):
        for ue in self.connected_ues:
            # dist = math.sqrt((self.x_pos - ue.x_pos)**2 + (self.y_pos - ue.y_pos)**2)
            dist = ue.current_dist

            # Determine base rate based on band
            base_rate = 0
            if dist <= HIGH_BAND_RANGE:
                base_rate = HIGH_BAND_THROUGHPUT
            elif dist <= MID_BAND_RANGE:
                base_rate = MID_BAND_THROUGHPUT
            elif dist <= LOW_BAND_RANGE:
                base_rate = LOW_BAND_THROUGHPUT

            # Adjust rate based on number of connected UEs
            num_ues = self.n_bands[ue.freq_band]
            if num_ues > 0:
                shared_rate = base_rate / num_ues
            else:
                shared_rate = base_rate

            ue.max_data_rate = shared_rate
            if self.verbose:
                print(f"IP_ADDR {int_to_ip(ue.ip_addr)}: Data rate == {ue.max_data_rate}")

            # Update the data rate dictionaries
            # ** I KNOW I AM SHARING A LOT OF VARIABLES 
            # BETWEEN CLASSES! Its just easier to do it this way **

            # Keep track of the data rates of each UE
            # if we have too much data to send to it, 
            # dump it and rely on ARQ for RETX
            self.ue_rates = {}
            self.ue_tx_bits = {}
            for ue in self.connected_ues:
                ue_max_rate = ue.max_data_rate*self.t_delta*ue.code_rate
                self.ue_rates[ue.ip_addr] = ue_max_rate
                self.ue_tx_bits[ue.ip_addr] = 0

    # Noisy dropout function that uses the code rate
    # set by the UE. The UE class has the same function
    # for transmitting over a noisy channel
    def noisy_dropout(self, ue, simulate_noise=False):
        if not simulate_noise:
            return False
        else:
            # Noise curve generated via ChadGPT
            x = ue.current_dist / ue.max_range
            base_loss = min(1.0, x * x)  # quadratic loss curve
            drop_prob = base_loss * ue.code_rate * 7e-2
            # End ChadGPT
            return random.random() < drop_prob

    # Need src and dest IP addr here since we need to know where
    # the bytes come from and where they are going (the tower acts
    # as a router)
    # ****NOTE: GIVEN THE DESTINATION IP, WE NEED TO REFERENCE
    # A ROUTING TABLE TO UPDATE THE THRU IP ADDR. The thru ip is 
    # saying what IP we need to go through to reach the dest IP.
    # Outputs:
    #       A return of True means successful receive
    #       A return of False means the tower buffer was full
    def receive(self, packet):
        """
        Packet BEFORE tower: 8 fields.
        Tower adds field 8 = thru_ip.
        """

        pkt_att = packet[7]

        # DROP packet if too many hops (TTL behavior)
        if pkt_att >= self.tx_attempts:
            return False

        pkt_bytes = packet[3]
        n_bits = len(pkt_bytes) * 8

        # tower buffer overflow?
        if (self.n_rx_bytes * 8) + n_bits > self.buff_thresh:
            return False

        self.n_rx_bytes += len(pkt_bytes)

        # Build tower-side packet
        packet_copy = list(packet)

        # increment tx_att (hop count)
        packet_copy[7] += 1

        # add/update thru_ip
        if len(packet_copy) == 8:
            packet_copy.append(self.ip_addr)
        else:
            packet_copy[8] = self.ip_addr

        self.buffer.appendleft(packet_copy)
        return True


    # Automatically checks the ingress packets and goes straight 
    # to re-transmitting (routing) them.
    # Check if bytes need to be sent. Also check the data buffer to see 
    # where the bytes need to go.
    # Packet format is: [t_step, packet_num, packet_type, n_bytes, src_ip, dest_ip, thru_ip, retx, tx_att]
    def transmit(self, simulate_noise=False):
        """
        Take one packet from the tower buffer and either:
          • Deliver it to a local UE, or
          • Forward it to another tower.

        Now supports forwarding ACK packets as well, so that ACKs can reach
        UEs connected to *other* towers.
        """

        # Nothing to send
        if self.n_rx_bytes <= 0 or len(self.buffer) == 0:
            return

        # Get the oldest packet (right side of deque)
        packet = self.buffer.pop()

        t_step     = packet[0]
        packet_num = packet[1]
        packet_type= packet[2]  # 0 = ACK, 1 = DATA
        pkt_bytes  = packet[3]
        src_ip     = packet[4]
        dest_ip    = packet[5]
        retx       = packet[6]
        tx_att     = packet[7]
        thru_ip    = packet[8]   # previous-hop tower

        pkt_len  = len(pkt_bytes)
        pkt_bits = pkt_len * 8

        # Drop if hop-count / TTL exceeded
        if tx_att >= self.tx_attempts:
            return

        # Remove these bytes from RX buffer
        self.n_rx_bytes -= pkt_len
        if self.n_rx_bytes < 0:
            self.n_rx_bytes = 0  # safety clamp

        # ----------------------------------------------------
        # 1) HANDLE ACK PACKETS (packet_type == 0)
        # ----------------------------------------------------
        if packet_type == 0:
            delivered = False

            # Try to deliver to a locally connected UE first
            for ue in self.connected_ues:
                if ue.ip_addr == dest_ip:
                    # Strip tower-only fields before passing to UE
                    clean = packet[:8]
                    ue.receive(clean)

                    delivered = True
                    self.n_tx_bytes    += pkt_len
                    self.total_bit_tx  += pkt_bits
                    break

            # If the ACK's destination UE is not on this tower,
            # forward the ACK to other towers (backhaul routing).
            if not delivered:
                for tower in self.connected_towers:
                    # Do not send back where it came from
                    if tower.ip_addr == thru_ip:
                        continue

                    tower.receive(packet)
                    self.n_tx_bytes   += pkt_len
                    self.total_bit_tx += pkt_bits

            # Nothing more to do for ACKs
            return

        # ----------------------------------------------------
        # 2) HANDLE DATA PACKETS (packet_type == 1)
        # ----------------------------------------------------
        delivered = False

        # Try to deliver to a locally connected UE
        for ue in self.connected_ues:

            # Do NOT send back to sender
            if ue.ip_addr == src_ip:
                continue

            if ue.ip_addr == dest_ip or dest_ip == self.broadcast_ip:

                # Channel model / possible dropout
                if not self.noisy_dropout(ue, simulate_noise):

                    # Enforce per-UE throughput budget
                    if self.ue_tx_bits[ue.ip_addr] + pkt_bits <= self.ue_rates[ue.ip_addr]:
                        clean = packet[:8]
                        ue.receive(clean)
                        self.ue_tx_bits[ue.ip_addr] += pkt_bits

                else:
                    # Count bit errors if dropped by noise
                    self.bit_errors += pkt_bits * (ue.current_dist / ue.max_range) * 1e-2

                delivered = True
                self.n_tx_bytes   += pkt_len
                self.total_bit_tx += pkt_bits
                break

        # ----------------------------------------------------
        # 3) FORWARD DATA TO OTHER TOWERS IF NOT DELIVERED
        # ----------------------------------------------------
        if not delivered:
            for tower in self.connected_towers:

                # Do NOT send backwards
                if tower.ip_addr == thru_ip:
                    continue

                tower.receive(packet)
                self.n_tx_bytes   += pkt_len
                self.total_bit_tx += pkt_bits



    # Continuously transmit data that is buffered until the 
    # data-rate limit has been reached
    def can_transmit(self):
        # If the buffer is empty, we cannot send data
        if len(self.buffer) == 0:
            return False

        # Size of next packet in bytes
        next_pkt = self.buffer[-1]
        next_len = len(next_pkt[3])  # packet_bytes length

        # If next transmission exceeds data rate limit
        if self.n_tx_bytes + next_len > (self.max_data_rate * self.t_delta):
            print("tower full of tx data")
            return False

        return True


    # Clear the tx byte counter after each step. This counter
    # is used to indicate whether or not the tower can transmit
    # at its full capacity
    def clear_tx_count(self):
        self.n_tx_bytes = 0

    # Function to set a connection between two towers
    def connect_tower(self, tower):
        assert tower is not self
        self.connected_towers.append(tower)
        tower.connected_towers.append(self)

    def step(self, simulate_noise=False):
        self.transmit(simulate_noise)
        self.ber = self.bit_errors / self.total_bit_tx
