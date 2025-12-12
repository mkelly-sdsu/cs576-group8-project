import random
import math
from collections import deque

def int_to_ip(x):
    return f"{(x >> 24) & 0xFF}.{(x >> 16) & 0xFF}.{(x >> 8) & 0xFF}.{x & 0xFF}"

# DEFINE IPv4 INDEX FIELDS
VERSION_IDX   =  0
IHL_IDX       =  1
TOS_IDX       =  2
TOTAL_LEN_IDX =  3
ID_IDX        =  4
FLAGS_IDX     =  5
FRAG_OFF_IDX  =  6
TTL_IDX       =  7
PROTOCOL_IDX  =  8
CHECKSUM_IDX  =  9
SRC_ADDR_IDX  = 10
DEST_ADDR_IDX = 11
OPTIONS_IDX   = 12
DATA_IDX      = 13

# RANGE Parameters
# High band (mmWave)
HIGH_BAND_RANGE = 300 #meters
# Mid band (sub-6 GHz)
MID_BAND_RANGE = 1500 #meters
# Low band (sub-1 GHz)
LOW_BAND_RANGE = 5000 #meters

#Distance helper function
def distance(a, b):
    return math.sqrt((a.x_pos - b.x_pos)**2 + (a.y_pos - b.y_pos)**2)

# Checksum helper function for IPv4 header checksum
def ipv4_checksum(header_bytes):
    if len(header_bytes) % 2 == 1:
        header_bytes += b'\x00'

    total = 0
    for i in range(0, len(header_bytes), 2):
        word = (header_bytes[i] << 8) + header_bytes[i+1]
        total += word
        total = (total & 0xFFFF) + (total >> 16)

    return (~total) & 0xFFFF

#User Equipment Class
class UE:
    def __init__(self, ue_id, x_pos, y_pos, towers, t_delta=None, ip_addr=None, verbose=True):
        # self.env = env
        self.ue_id = ue_id # UE identifier
        self.x_pos = x_pos # x position of UE
        self.y_pos = y_pos # y position of UE
        self.towers   = towers # list of available towers
        self.n_towers = len(towers)
        self.current_tower = None # currently connected tower
        self.current_dist  = 0 # Distance from current tower
        self.steps_in_current_direction = 0
        self.dx = 0
        self.dy = 0
        # Remove this for now
        # self.action = env.process(self.run())
        
        # new variables
        self.verbose = verbose
        # List of distances between the UE and each tower
        self.distances = []
        # print(t_delta)
        assert t_delta is not None
        self.t_delta = t_delta
        # The tower should define this. This Data Rate depends on how many
        # devices (UEs) are currently connected to the tower. Data Rate is
        # in bits/sec (bps)
        # self.max_data_rate = 5000000 # in bits-per-second
        self.max_data_rate = 0 # in bits-per-second (0 means unconnected)
        self.n_tx_bytes = 0
        # self.n_rx_bytes = 0
        assert ip_addr is not None
        self.ip_addr = ip_addr
        self.buffer  = deque([]) # Deque of how many bytes to tx and where they are going to.
        self.buff_thresh = 1e9 # assume a 1Gb buffer (max possible data rate)
        self.n_tx_bits = 0 # Number of bits ready to be sent from the UE buffer
        # The following variables are used for ARQ
        self.t_step  = 0
        self.arq_timeout = 5 # Number of simpy timesteps until a packet is considered timed out
        self.arq_retx    = 3 # Re-transmission attempts. If set to 0, ARQ is disabled
        self.packet_num = 0 # Increment the packet number after every Tx
        self.freq_band = None # What frequency band the UE is using
        self.code_rate = 0.9 # 0.9 is default for 5G apparently
        self.max_range = 0
        self.broadcast_ip = 65535
        self.tx_bytes_step = 0 # Number of transmitted bytes per timestep
        self.ber = 0 # Bit error rate counter that gets incremented/reset each timestep
                     # this gets incremented if there is a simulated dropout from noise
        self.total_bit_tx = 1 # Cumulative transmitted bits
        self.bit_errors = 0 # Cumulative bit-error count per timestep

    # Function to calculate the distances between the UE and all towers.
    # Run this function every timestep
    def calculate_dist(self):
        distances = list(map(lambda t: distance(self, t), self.towers))
        # print(f"UE {self.ip_addr}: Distances = {distances}")
        self.distances = distances

    # Function to update the towers list in case towers are added/removed
    def update_towers(self, towers):
        self.towers   = towers
        self.n_towers = len(towers)
        if self.n_towers == 0:
            if self.current_tower is not None:
                # remove the ue from the band
                self.current_tower.n_bands[self.freq_band] -= 1
                self.current_tower.connected_ues.remove(self)
            self.freq_band = None
            self.current_tower = None
            self.max_data_rate = 0

    def connect_to_best_tower(self):
        # No towers available at all
        if self.n_towers == 0 or len(self.towers) == 0:
            if self.current_tower is not None and self.freq_band is not None:
                # Safely detach from current tower
                if self in self.current_tower.connected_ues:
                    self.current_tower.connected_ues.remove(self)
                if self.freq_band in self.current_tower.n_bands:
                    self.current_tower.n_bands[self.freq_band] = max(
                        0, self.current_tower.n_bands[self.freq_band] - 1
                    )
            self.current_tower = None
            self.freq_band = None
            self.max_range = 0
            self.max_data_rate = 0
            return

        # Distance from best tower
        min_dist = min(self.distances)
        best_tower_idx = self.distances.index(min_dist)
        best_tower = self.towers[best_tower_idx]
        self.current_dist = min_dist

        # Helper to choose band + max_range
        def select_band(d):
            if d <= HIGH_BAND_RANGE * 0.7:
                return "high", HIGH_BAND_RANGE
            elif d <= MID_BAND_RANGE * 0.9:
                return "mid", MID_BAND_RANGE
            elif d <= LOW_BAND_RANGE:
                return "low", LOW_BAND_RANGE
            else:
                return None, 0

        # If the current tower is not the best tower → possible handover
        if self.current_tower != best_tower:
            # Detach from current tower if any
            if self.current_tower is not None and self.freq_band is not None:
                if self in self.current_tower.connected_ues:
                    self.current_tower.connected_ues.remove(self)
                if self.freq_band in self.current_tower.n_bands:
                    self.current_tower.n_bands[self.freq_band] = max(
                        0, self.current_tower.n_bands[self.freq_band] - 1
                    )

            new_band, new_range = select_band(min_dist)

            if new_band is None:
                # Out of range of all bands
                if self.current_tower is not None:
                    print(f"UE {int_to_ip(self.ip_addr)}: Lost connection to Tower {int_to_ip(self.current_tower.ip_addr)}")
                self.current_tower = None
                self.freq_band = None
                self.max_range = 0
                self.max_data_rate = 0
                return

            # Attach to new/best tower
            if self.current_tower is not None:
                print(f"UE {self.ue_id} handover from Tower {self.current_tower.tower_id} to {best_tower.tower_id}")
            else:
                print(f"UE {self.ue_id} initially connecting to Tower {best_tower.tower_id}")

            self.current_tower = best_tower
            self.freq_band = new_band
            self.max_range = new_range

            if self.freq_band in self.current_tower.n_bands:
                self.current_tower.n_bands[self.freq_band] += 1
            else:
                self.current_tower.n_bands[self.freq_band] = 1

            if self not in self.current_tower.connected_ues:
                self.current_tower.connected_ues.append(self)

            self.current_tower.set_data_rate()
            return

        # If the current tower is already the best tower
        prev_freq = self.freq_band

        # Re-select band using full ranges (no 0.7/0.9 hysteresis here)
        if min_dist <= HIGH_BAND_RANGE:
            self.freq_band = "high"
            self.max_range = HIGH_BAND_RANGE
        elif min_dist <= MID_BAND_RANGE:
            self.freq_band = "mid"
            self.max_range = MID_BAND_RANGE
        elif min_dist <= LOW_BAND_RANGE:
            self.freq_band = "low"
            self.max_range = LOW_BAND_RANGE
        else:
            # Out of range → detach
            if self.current_tower is not None and self.freq_band is not None:
                if self in self.current_tower.connected_ues:
                    self.current_tower.connected_ues.remove(self)
                if self.freq_band in self.current_tower.n_bands:
                    self.current_tower.n_bands[self.freq_band] = max(
                        0, self.current_tower.n_bands[self.freq_band] - 1
                    )
                print(f"UE {int_to_ip(self.ip_addr)}: Lost connection to Tower {int_to_ip(self.current_tower.ip_addr)}")
            self.current_tower = None
            self.freq_band = None
            self.max_range = 0
            self.max_data_rate = 0
            return

        # Still on same tower, but band may have changed
        if self.current_tower is not None and prev_freq != self.freq_band:
            if prev_freq in self.current_tower.n_bands:
                self.current_tower.n_bands[prev_freq] = max(
                    0, self.current_tower.n_bands[prev_freq] - 1
                )
            if self.freq_band in self.current_tower.n_bands:
                self.current_tower.n_bands[self.freq_band] += 1
            else:
                self.current_tower.n_bands[self.freq_band] = 1

        if self.current_tower is not None:
            self.current_tower.set_data_rate()
        else:
            self.max_data_rate = 0


    # Test moving the UEs in and out of coverage
    def move(self):
        self.x_pos = random.uniform(-3000, 3000)
        self.y_pos = random.uniform(-3000, 3000)
        # print(f"Distance from (0,0): {math.sqrt((self.x_pos)**2 + (self.y_pos)**2)}")

    def set_code_rate(self):
        if self.current_tower is not None:
            # This ratio will be used to set the different LDPC code rates
            ratio = self.current_dist / self.max_range
            # Piecewise function to select LDPC code rates
            if ratio <= 0.3:
                self.code_rate = 0.9
            elif ratio <= 0.7:
                self.code_rate = 2/3
            else:
                self.code_rate = 0.5
        else:
            self.code_rate = 0.9

    def noisy_dropout(self, simulate_noise=False):
        if not simulate_noise:
            return False
        else: 
            if self.current_tower is not None:
                # Noise curve generated via ChadGPT
                x = self.current_dist / self.max_range
                base_loss = min(1.0, x * x)  # quadratic loss curve
                drop_prob = base_loss * self.code_rate * 7e-2
                # End ChadGPT
                return random.random() < drop_prob
            # Don't really need this here but
            # include for sake of completion
            else:
                return False

    # Split the fields into header and data.
    # Header IDX's:
    #    [ 0] - Version (Set to 4)           -  4-bits
    #    [ 1] - Internet Header Length (IHL) -  4-bits
    #    [ 2] - Type of Service (ToS)        -  8-bits
    #    [ 3] - Total Length (header + data) - 16-bits
    #    [ 4] - Identification (fragment)    - 16-bits
    #    [ 5] - Flags                        -  3-bits
    #    [ 6] - Fragment Offset              - 13-bits
    #    [ 7] - Time to Live (TTL)           -  8-bits
    #    [ 8] - Protocol (TCP=6, UDP=17)     -  8-bits
    #    [ 9] - Header Checksum              - 16-bits
    #    [10] - Source Address               - 32-bits
    #    [11] - Destination Address          - 32-bits
    #    [12] - Options                      - 0->40 bytes
    def set_cust_data(self, header, data):
        # --- Extract options ---
        options = header[OPTIONS_IDX]
        if options is None:
            options = b""
        if isinstance(options, int):
            # convert int to bytes if needed
            options = options.to_bytes((options.bit_length() + 7) // 8, 'big')

        # --- Ensure option padding: must be multiple of 4 bytes ---
        if len(options) % 4 != 0:
            pad = 4 - (len(options) % 4)
            options += b"\x00" * pad

        options_len = len(options)

        # --- Compute IHL (5 + number_of_32bit_words_in_options) ---
        ihl = 5 + (options_len // 4)
        header_length = ihl * 4   # bytes

        # --- Allocate packet header ---
        packet = bytearray(header_length)

        # ========= Fixed Header Packing ==========

        # Version + IHL
        version = header[VERSION_IDX] & 0xF
        packet[0] = (version << 4) | (ihl & 0xF)

        # TOS
        packet[1] = header[TOS_IDX] & 0xFF

        # Total Length (header + data)
        total_len = header_length + len(data)
        packet[2] = (total_len >> 8) & 0xFF
        packet[3] = total_len & 0xFF

        # Identification
        ident = header[ID_IDX] & 0xFFFF
        packet[4] = (ident >> 8) & 0xFF
        packet[5] = ident & 0xFF

        # Flags + Frag Offset
        flags = header[FLAGS_IDX] & 0x7
        frag = header[FRAG_OFF_IDX] & 0x1FFF
        combined = (flags << 13) | frag
        packet[6] = (combined >> 8) & 0xFF
        packet[7] = combined & 0xFF

        # TTL + Protocol
        packet[8] = header[TTL_IDX] & 0xFF
        packet[9] = header[PROTOCOL_IDX] & 0xFF

        # Checksum (placeholder: 0)
        packet[10] = 0
        packet[11] = 0

        # Source address
        src = header[SRC_ADDR_IDX] & 0xFFFFFFFF
        packet[12] = (src >> 24) & 0xFF
        packet[13] = (src >> 16) & 0xFF
        packet[14] = (src >>  8) & 0xFF
        packet[15] =  src        & 0xFF

        # Dest address
        dst = header[DEST_ADDR_IDX] & 0xFFFFFFFF
        packet[16] = (dst >> 24) & 0xFF
        packet[17] = (dst >> 16) & 0xFF
        packet[18] = (dst >>  8) & 0xFF
        packet[19] =  dst        & 0xFF

        # ========= Insert Options =========
        if options_len > 0:
            packet[20:20+options_len] = options

        # ========= Compute and insert checksum =========
        checksum = ipv4_checksum(packet)
        packet[10] = (checksum >> 8) & 0xFF
        packet[11] = checksum & 0xFF

        # ========= Return FULL PACKET (header + data) =========
        return packet + data


    # NOTE: packet_type of 1 means a data packet, while a 0 is an ACK packet.
    # We only send ack packets when data packets are received.
    # Set the tx_att to 0 (at end). This indicates the number of hops the packet has traveled.
    # Split the fields into header and data.
    # Header IDX's:
    #    [ 0] - Version (Set to 4)           -  4-bits
    #    [ 1] - Internet Header Length (IHL) -  4-bits
    #    [ 2] - Type of Service (ToS)        -  8-bits
    #    [ 3] - Total Length (header + data) - 16-bits
    #    [ 4] - Identification (fragment)    - 16-bits
    #    [ 5] - Flags                        -  3-bits
    #    [ 6] - Fragment Offset              - 13-bits
    #    [ 7] - Time to Live (TTL)           -  8-bits
    #    [ 8] - Protocol (TCP=6, UDP=17)     -  8-bits
    #    [ 9] - Header Checksum              - 16-bits
    #    [10] - Source Address               - 32-bits
    #    [11] - Destination Address          - 32-bits
    #    [12] - Options                      - 0->40 bytes
    def set_tx_bytes(self, n_bytes, dest_ip=None, payload=None):
        assert dest_ip is not None

        bytes_remaining = n_bytes

        # If caller provides a payload, use it; otherwise generate dummy data
        if payload is None:
            payload = bytes([0] * n_bytes)

        # Fragmentation limit (IPv4 max without options)
        MAX_FRAGMENT_SIZE = 65535 - 20

        # Process the provided payload in chunks
        offset = 0
        while bytes_remaining > 0:
            frag_size = min(MAX_FRAGMENT_SIZE, bytes_remaining)
            data = payload[offset : offset + frag_size]

            header = {
                VERSION_IDX:   4,
                IHL_IDX:       5,
                TOS_IDX:       0,
                TOTAL_LEN_IDX: 20 + frag_size,
                ID_IDX:        self.packet_num & 0xFFFF,
                FLAGS_IDX:     0,
                FRAG_OFF_IDX:  0,
                TTL_IDX:       64,
                PROTOCOL_IDX:  99,
                CHECKSUM_IDX:  0,
                SRC_ADDR_IDX:  self.ip_addr,
                DEST_ADDR_IDX: dest_ip,
                OPTIONS_IDX:   b"",
            }

            packet_bytes = self.set_cust_data(header, data)
            packet_bits = len(packet_bytes) * 8

            # Enqueue only if buffer capacity allows
            if packet_bits + self.n_tx_bits <= self.buff_thresh:

                pkt = [
                    self.t_step,           # last ARQ timestamp
                    self.packet_num,       # packet ID
                    1,                     # packet_type = data
                    packet_bytes,
                    self.ip_addr,          # src
                    dest_ip,               # dest
                    0,                     # retx counter
                    0                      # tx_att
                ]

                # --------------------------------------------------
                # FIFO QUEUE FIX ← append to END, not appendleft
                # --------------------------------------------------
                self.buffer.append(pkt)

                self.n_tx_bits += packet_bits
                self.packet_num += 1

            else:
                print(f"UE {int_to_ip(self.ip_addr)}: BUFFER FULL while adding fragments")
                break

            offset += frag_size
            bytes_remaining -= frag_size



    # Automatically checks the ingress packets and goes straight 
    # to re-transmitting (routing) them.
    # Check if bytes need to be sent. Also check the data buffer to see 
    # where the bytes need to go.
    def transmit(self, simulate_noise=False):
        """
        Correct ARQ-compliant FIFO transmit.
        - Sends ONLY the oldest packet once per timestep.
        - Packet stays in buffer until ACK or MAX RETX.
        - ARQ timeout and MAX RETX work again.
        """

        self.tx_bytes_step = 0

        # No packets → nothing to do
        if len(self.buffer) == 0:
            return

        # --------------------------------------------------------
        # 1) ARQ TIMEOUT CHECK FOR OLDEST PACKET (FIFO = buffer[0])
        # --------------------------------------------------------
        oldest = self.buffer[0]

        t_step     = oldest[0]
        packet_num = oldest[1]
        packet_type = oldest[2]
        pkt_bytes  = oldest[3]
        dest_ip    = oldest[5]
        retx       = oldest[6]

        pkt_bits = len(pkt_bytes) * 8
        pkt_len  = len(pkt_bytes)

        # Apply ARQ only to data (1), not ACK (0)
        if packet_type == 1 and self.arq_retx > 0 and dest_ip != self.broadcast_ip:

            # Check timeout
            if self.t_step - t_step >= self.arq_timeout:
                oldest[6] += 1     # RETX++
                oldest[0] = self.t_step

                # MAX RETX exceeded → DROP packet
                if oldest[6] > self.arq_retx:
                    dropped = self.buffer.popleft()
                    self.n_tx_bits -= len(dropped[3]) * 8

                    if self.verbose:
                        print(f"UE IP_ADDR {int_to_ip(self.ip_addr)}: MAX RETX REACHED. Dropped packet {packet_num}.")
                    return

        # --------------------------------------------------------
        # 2) If NO TOWER -> ARQ above runs, but do NOT transmit
        # --------------------------------------------------------
        if self.current_tower is None:
            return

        # --------------------------------------------------------
        # 3) SEND ONLY THE OLDEST PACKET ONCE (NO DUPLICATES)
        # --------------------------------------------------------
        bit_budget = self.max_data_rate * self.t_delta * self.code_rate

        # Enough throughput to send it?
        if pkt_bits <= bit_budget:

            # Try sending it
            if not self.noisy_dropout(simulate_noise):
                self.current_tower.receive(oldest)  # oldest stays in buffer
            else:
                if self.max_range > 0:
                    self.bit_errors += pkt_bits * (self.current_dist/self.max_range) * 1e-2

            # Update TX stats
            self.tx_bytes_step += pkt_len
            self.n_tx_bytes    += pkt_len
            self.total_bit_tx  += pkt_bits


    # Need to check for received bytes
    # If ARQ is enabled, we need to handle ACKs
    # Packet format is: [t_step, packet_num, packet_type, n_bytes, src_ip, dest_ip, retx]
    def receive(self, packet):
        """
        Clean, correct ACK + DATA processing for the NEW packet format:
        [t_step, packet_num, packet_type, pkt_bytes, src_ip, dest_ip, retx, tx_att]

        Fixes:
        - Repeated ACK storms
        - Data packet never being removed
        - Broken matching due to new packet structure
        """

        t_step     = packet[0]
        packet_num = packet[1]
        packet_type= packet[2]
        pkt_bytes  = packet[3]
        src_ip     = packet[4]
        dest_ip    = packet[5]
        retx       = packet[6]

        n_bytes = len(pkt_bytes)

        # ----------------------------
        # PRINT (unchanged)
        # ----------------------------
        if self.verbose:
            p_type = "data" if packet_type == 1 else "ack"
            print(f"UE IP_ADDR {int_to_ip(self.ip_addr)}: Received {p_type} packet "
                  f"of {n_bytes} bytes from device IP_ADDR {int_to_ip(src_ip)}")

        # ----------------------------
        # DATA PACKET RECEIVED → SEND ACK
        # ----------------------------
        if packet_type == 1:  # DATA

            # Build 1-byte ack payload
            ack_payload = b'\x00'

            header = {
                VERSION_IDX:   4,
                IHL_IDX:       5,
                TOS_IDX:       0,
                TOTAL_LEN_IDX: 20 + len(ack_payload),
                ID_IDX:        packet_num & 0xFFFF,
                FLAGS_IDX:     0,
                FRAG_OFF_IDX:  0,
                TTL_IDX:       64,
                PROTOCOL_IDX:  99,
                CHECKSUM_IDX:  0,
                SRC_ADDR_IDX:  self.ip_addr,
                DEST_ADDR_IDX: src_ip,
                OPTIONS_IDX:   b"",
            }

            ack_bytes = self.set_cust_data(header, ack_payload)

            ack_packet = [
                self.t_step,      # timestamp
                packet_num,       # must match DATA id
                0,                # packet_type = ACK
                ack_bytes,        # raw bytes
                self.ip_addr,     # src
                src_ip,           # dest
                retx,             # carry-through retx (legacy behavior)
                0                 # tx_att
            ]

            # ACK must be sent even if tower not connected (old behavior)
            if self.current_tower is not None:
                self.current_tower.receive(ack_packet)
                self.tx_bytes_step += len(ack_bytes)

            return  # STOP — DATA does not drop anything here

        # ==========================================================
        # ACK RECEIVED → REMOVE THE MATCHING DATA PACKET (FIFO SAFE)
        # ==========================================================
        if packet_type == 0:  # ACK

            for i, pkt in enumerate(self.buffer):
                pkt_num = pkt[1]
                pkt_type_inner = pkt[2]

                # Match DATA packet with same packet_num
                if pkt_num == packet_num and pkt_type_inner == 1:
                    removed = self.buffer[i]
                    del self.buffer[i]          # <-- SAFE: deque supports indexed delete
                    self.n_tx_bits -= len(removed[3]) * 8

                    if self.verbose:
                        print(f"UE IP_ADDR {int_to_ip(self.ip_addr)}: Received ACK. Dropped packet {pkt_num}.")
                    break

            return


    # Clear the tx byte counter after each step. This counter
    # is used to calculate the data rate
    def clear_tx_count(self):
        self.n_tx_bytes = 0
        self.tx_bytes_step = 0

    def clear_buffer(self):
        print(f"UE {int_to_ip(self.ip_addr)}: Clearing buffer")
        self.buffer.clear()

    # Step through each function you want to be performed
    # at each and every timestep
    def step(self, simulate_noise=False):
        # ALWAYS advance ARQ time
        self.t_step += 1

        # ALWAYS run ARQ + transmit logic
        self.transmit(simulate_noise)

        # Only run tower logic if towers exist
        if self.n_towers > 0:
            self.calculate_dist()
            self.connect_to_best_tower()
            self.set_code_rate()

        # BER update
        if self.total_bit_tx > 0:
            self.ber = self.bit_errors / self.total_bit_tx

