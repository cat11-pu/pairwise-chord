"""chord.core 的验收测试。"""

import unittest

from chord.core import ChordError, ChordRing

LAYOUT = (1, 4, 6, 9, 12)


def build_ring(*node_ids, bits=4):
    ring = ChordRing(bits=bits)
    for node_id in node_ids:
        ring.add_node(node_id)
    return ring


def expected_successor(ring, key_id):
    """按环的定义直接算出 key_id 的后继，不使用被测路由。"""
    ids = ring.node_ids()
    for node_id in ids:
        if node_id >= key_id:
            return node_id
    return ids[0]


def expected_holders(ring, key_id):
    """按环的定义直接算出 key_id 的持有者集合。"""
    holders = []
    current = expected_successor(ring, key_id)
    while len(holders) < ring.replicas and current not in holders:
        holders.append(current)
        current = expected_successor(ring, (current + 1) % ring.size)
    return sorted(holders)


class HashKeyTest(unittest.TestCase):
    def test_hash_key_covers_ring_space(self):
        ring = ChordRing(bits=4)
        self.assertEqual(ring.size, 16)
        positions = set()
        for index in range(500):
            position = ring.hash_key("chord-key-%03d" % index)
            self.assertIsInstance(position, int)
            self.assertTrue(0 <= position < ring.size)
            positions.add(position)
        self.assertEqual(positions, set(range(ring.size)))
        self.assertEqual(ring.hash_key("dht"), 15)
        self.assertEqual(ring.hash_key("key"), 3)


class FingerTableTest(unittest.TestCase):
    def test_finger_table_layout(self):
        ring = build_ring(*LAYOUT)
        self.assertEqual(ring.fingers_of(1), [4, 4, 6, 9])
        self.assertEqual(ring.fingers_of(4), [6, 6, 9, 12])
        self.assertEqual(ring.fingers_of(6), [9, 9, 12, 1])
        self.assertEqual(ring.fingers_of(9), [12, 12, 1, 1])
        self.assertEqual(ring.fingers_of(12), [1, 1, 1, 4])


class FingerSelectionTest(unittest.TestCase):
    def test_closest_preceding_finger(self):
        ring = build_ring(*LAYOUT)
        self.assertEqual(ring.closest_preceding_finger(6, 10), 9)
        self.assertEqual(ring.closest_preceding_finger(9, 15), 12)
        self.assertEqual(ring.closest_preceding_finger(9, 5), 1)
        self.assertEqual(ring.closest_preceding_finger(12, 4), 4)
        self.assertIsNone(ring.closest_preceding_finger(1, 3))


class LookupTest(unittest.TestCase):
    def test_lookup_matches_successor_and_owner(self):
        for ring in (build_ring(*LAYOUT), build_ring(2, 11), build_ring(0, 5, 10, 15)):
            for key_id in range(ring.size):
                expected = expected_successor(ring, key_id)
                self.assertEqual(ring.lookup(key_id), expected, "键 %d" % key_id)
                for start in ring.node_ids():
                    self.assertEqual(
                        ring.lookup(key_id, start),
                        expected,
                        "键 %d 起点 %d" % (key_id, start),
                    )
                owners = [
                    node_id for node_id in ring.node_ids() if ring.owns(node_id, key_id)
                ]
                self.assertEqual(len(owners), 1, "键 %d 的归属 %r" % (key_id, owners))
                self.assertEqual(owners[0], expected)


class SingleNodeTest(unittest.TestCase):
    def test_single_node_ring_serves_every_key(self):
        ring = ChordRing(bits=4)
        ring.add_node(3)
        self.assertEqual(ring.node_ids(), [3])
        self.assertEqual(ring.successor_of(3), 3)
        self.assertEqual(ring.predecessor_of(3), 3)
        self.assertEqual(ring.fingers_of(3), [3, 3, 3, 3])
        for key_id in (0, 3, 7, 15):
            self.assertEqual(ring.lookup(key_id), 3, "键 %d" % key_id)
        key_id = ring.hash_key("solo")
        ring.store(key_id, "solo-value")
        self.assertEqual(ring.get(key_id), "solo-value")
        self.assertEqual(ring.holders_of(key_id), [3])
        self.assertEqual(ring.keys_of(3), [key_id])


class JoinFingerTest(unittest.TestCase):
    def test_join_refreshes_other_finger_tables(self):
        ring = build_ring(*LAYOUT)
        self.assertEqual(ring.fingers_of(1), [4, 4, 6, 9])
        self.assertEqual(ring.fingers_of(4), [6, 6, 9, 12])
        ring.add_node(3)
        self.assertEqual(ring.node_ids(), [1, 3, 4, 6, 9, 12])
        self.assertEqual(ring.fingers_of(1), [3, 3, 6, 9])
        self.assertEqual(ring.fingers_of(3), [4, 6, 9, 12])
        ring.add_node(5)
        self.assertEqual(ring.node_ids(), [1, 3, 4, 5, 6, 9, 12])
        self.assertEqual(ring.fingers_of(4), [5, 6, 9, 12])
        self.assertEqual(ring.fingers_of(1), [3, 3, 5, 9])


class JoinDataTest(unittest.TestCase):
    def test_join_relocates_keys_with_copies(self):
        ring = build_ring(1, 6, 12)
        values = {}
        for key_id in range(ring.size):
            value = "值-%d" % key_id
            values[key_id] = value
            ring.store(key_id, value)
        self.assertEqual(sorted(values), list(range(ring.size)))
        ring.add_node(4)
        self.assertEqual(ring.node_ids(), [1, 4, 6, 12])
        for key_id, value in sorted(values.items()):
            self.assertEqual(ring.get(key_id), value, "键 %d" % key_id)
            self.assertEqual(
                ring.holders_of(key_id), expected_holders(ring, key_id), "键 %d" % key_id
            )


class LeaveTest(unittest.TestCase):
    def test_leave_hands_keys_over(self):
        ring = build_ring(*LAYOUT)
        values = {}
        for key_id in range(ring.size):
            value = "值-%d" % key_id
            values[key_id] = value
            ring.store(key_id, value)
        ring.remove_node(9)
        self.assertEqual(ring.node_ids(), [1, 4, 6, 12])
        for key_id, value in sorted(values.items()):
            self.assertEqual(ring.get(key_id), value, "键 %d" % key_id)
            self.assertEqual(
                ring.holders_of(key_id), expected_holders(ring, key_id), "键 %d" % key_id
            )


class BadInputTest(unittest.TestCase):
    def test_invalid_inputs(self):
        ring = ChordRing(bits=4)
        with self.assertRaises(ChordError):
            ring.lookup(3)
        with self.assertRaises(ChordError):
            ring.hash_key("")
        with self.assertRaises(TypeError):
            ring.hash_key(42)
        with self.assertRaises(ChordError):
            ring.add_node(-1)
        with self.assertRaises(ChordError):
            ring.add_node(16)
        with self.assertRaises(TypeError):
            ring.add_node("3")
        ring.add_node(3)
        with self.assertRaises(ChordError):
            ring.add_node(3)
        with self.assertRaises(ChordError):
            ring.remove_node(4)
        with self.assertRaises(ChordError):
            ring.lookup(16)
        with self.assertRaises(ChordError):
            ring.store(-1, "值")
        with self.assertRaises(ChordError):
            ring.get(16)
        with self.assertRaises(ChordError):
            ring.fingers_of(4)
        with self.assertRaises(ChordError):
            ring.owns(4, 1)


if __name__ == "__main__":
    unittest.main()
