"""纯内存版 Chord 一致性哈希环仿真内核。

模块只依赖 Python 标准库，节点、finger 表与键值数据全部保存在进程内存中，
不涉及任何真实网络通信，方便验证一致性哈希的查找与迁移逻辑。
"""

import hashlib

DEFAULT_BITS = 4
DEFAULT_REPLICAS = 2


class ChordError(Exception):
    """环操作相关的错误。"""


class ChordNode:
    """环上的一个节点，只保存编号与相邻节点指针。"""

    __slots__ = ("node_id", "successor", "predecessor", "fingers")

    def __init__(self, node_id):
        self.node_id = node_id
        self.successor = None
        self.predecessor = None
        self.fingers = []

    def __repr__(self):
        return "ChordNode({})".format(self.node_id)


class ChordRing:
    """一致性哈希环，支持节点加入、退出与键值读写。"""

    def __init__(self, bits=DEFAULT_BITS, replicas=DEFAULT_REPLICAS):
        if isinstance(bits, bool) or not isinstance(bits, int) or bits < 1:
            raise ChordError("环位数必须是正整数")
        if isinstance(replicas, bool) or not isinstance(replicas, int) or replicas < 1:
            raise ChordError("副本数必须是正整数")
        self.bits = bits
        self.size = 1 << bits
        self.replicas = replicas
        self.nodes = {}
        self.holdings = {}

    # ------------------------------------------------------------- 环空间
    def hash_key(self, text):
        """把文本映射到环上的一个位置。"""
        if not isinstance(text, str):
            raise TypeError("键必须是字符串")
        if not text:
            raise ChordError("键不能是空字符串")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return int(digest, 16) % self.size

    def node_ids(self):
        """按编号升序返回环上所有节点。"""
        return sorted(self.nodes)

    def _require_position(self, value, label):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("{} 必须是整数".format(label))
        if value < 0 or value >= self.size:
            raise ChordError("{} {} 超出环空间".format(label, value))
        return value

    def _require_node(self, node_id):
        self._require_position(node_id, "节点编号")
        if node_id not in self.nodes:
            raise ChordError("节点 {} 不在环上".format(node_id))
        return node_id

    def _first_node(self):
        if not self.nodes:
            raise ChordError("环上还没有节点")
        return min(self.nodes)

    # ------------------------------------------------------------- 相邻关系
    def successor_of(self, node_id):
        """返回 node_id 在环上的后继节点编号。"""
        self._require_node(node_id)
        return self.nodes[node_id].successor

    def predecessor_of(self, node_id):
        """返回 node_id 在环上的前驱节点编号。"""
        self._require_node(node_id)
        return self.nodes[node_id].predecessor

    # ------------------------------------------------------------- 区间判定
    def _in_interval(self, position, start, end):
        """判断 position 是否落在环上 (start, end] 区间内。"""
        if start < end:
            return start < position <= end
        if start > end:
            return position > start or position <= end
        return True

    def owns(self, node_id, key_id):
        """判断 key_id 是否应当由 node_id 负责。"""
        self._require_node(node_id)
        self._require_position(key_id, "键编号")
        return self._in_interval(key_id, self.predecessor_of(node_id), node_id)

    # ------------------------------------------------------------- finger 表
    def fingers_of(self, node_id):
        """返回 node_id 的 finger 表。"""
        self._require_node(node_id)
        return list(self.nodes[node_id].fingers)

    def _closest_successor(self, position, start):
        """不做路由，沿后继指针逐个前进求 position 的后继。"""
        current = start
        for _ in range(len(self.nodes) + 1):
            if position == current:
                return current
            succ = self.successor_of(current)
            if succ is None:
                return None
            if self._in_interval(position, current, succ):
                return succ
            current = succ
        return None

    def _rebuild_fingers(self, node_id):
        """重算 node_id 的 finger 表。"""
        table = []
        for index in range(self.bits):
            offset = 1 << index
            position = (node_id + offset) % self.size
            table.append(self._closest_successor(position, node_id))
        self.nodes[node_id].fingers = table

    def _rebuild_all_fingers(self):
        for node_id in self.node_ids():
            self._rebuild_fingers(node_id)

    def closest_preceding_finger(self, node_id, target):
        """在 finger 表中找出不超过 target 的节点编号，没有时返回 None。"""
        self._require_node(node_id)
        self._require_position(target, "目标位置")
        best = None
        best_distance = -1
        for finger in self.fingers_of(node_id):
            if finger is None:
                continue
            if finger == node_id:
                continue
            if not self._in_interval(finger, node_id, target):
                continue
            distance = self._distance(node_id, finger)
            if distance > best_distance:
                best = finger
                best_distance = distance
        return best

    # ------------------------------------------------------------- 查找
    def lookup(self, key_id, start=None):
        """返回负责 key_id 的节点编号。"""
        self._require_position(key_id, "键编号")
        if not self.nodes:
            raise ChordError("环上还没有节点")
        pred = self._find_predecessor(key_id, start)
        if pred is None:
            return None
        return self.successor_of(pred)

    def _find_predecessor(self, key_id, start):
        """从 start 出发沿 finger 表逼近 key_id 的前驱。"""
        current = self._first_node() if start is None else start
        self._require_node(current)
        for _ in range(2 * self.size):
            succ = self.successor_of(current)
            if succ is None:
                return None
            if self._in_interval(key_id, current, succ):
                return current
            nxt = self.closest_preceding_finger(current, key_id)
            if nxt is None or nxt == current:
                nxt = succ
            elif self._distance(current, nxt) >= self._distance(current, key_id):
                nxt = succ
            current = nxt
        return None

    def _distance(self, start, end):
        """环上从 start 顺时针到 end 的距离。"""
        return (end - start) % self.size

    # ------------------------------------------------------------- 键值数据
    def _holder_chain(self, owner_id):
        """从归属节点出发沿后继方向取互不相同的副本节点。"""
        chain = []
        current = owner_id
        while len(chain) < self.replicas and current is not None and current not in chain:
            chain.append(current)
            current = self.successor_of(current)
        return chain

    def store(self, key_id, value):
        """把 value 写到 key_id 的归属节点与其后继副本节点上。"""
        self._require_position(key_id, "键编号")
        owner = self.lookup(key_id)
        if owner is None:
            raise ChordError("无法为键 {} 定位归属节点".format(key_id))
        for node_id in self._holder_chain(owner):
            self.holdings[node_id][key_id] = value
        return owner

    def get(self, key_id):
        """读取 key_id 的值，归属节点上缺失时回退到副本节点。"""
        self._require_position(key_id, "键编号")
        owner = self.lookup(key_id)
        if owner is None:
            raise ChordError("无法为键 {} 定位归属节点".format(key_id))
        for node_id in self._holder_chain(owner):
            data = self.holdings[node_id]
            if key_id in data:
                return data[key_id]
        raise KeyError(key_id)

    def holders_of(self, key_id):
        """返回当前持有 key_id 的节点编号。"""
        self._require_position(key_id, "键编号")
        return sorted(node_id for node_id, data in self.holdings.items() if key_id in data)

    def keys_of(self, node_id):
        """返回节点当前持有的键编号。"""
        self._require_node(node_id)
        return sorted(self.holdings[node_id])

    # ------------------------------------------------------------- 拓扑变化
    def add_node(self, node_id):
        """把新节点加入环，并接管本应由它负责的键。"""
        self._require_position(node_id, "节点编号")
        if node_id in self.nodes:
            raise ChordError("节点 {} 已在环上".format(node_id))
        node = ChordNode(node_id)
        if not self.nodes:
            self.nodes[node_id] = node
            self.holdings[node_id] = {}
            node.successor = node_id
            node.predecessor = node_id
            self._rebuild_fingers(node_id)
            return node_id
        succ = self.lookup(node_id)
        if succ is None:
            raise ChordError("无法为新节点 {} 定位后继".format(node_id))
        pred = self.nodes[succ].predecessor
        self.nodes[node_id] = node
        self.holdings[node_id] = {}
        node.successor = succ
        node.predecessor = pred
        self.nodes[pred].successor = node_id
        self.nodes[succ].predecessor = node_id
        self._rebuild_all_fingers()
        self._redistribute()
        return node_id

    def remove_node(self, node_id):
        """把节点从环上摘除，并处理它持有的键。"""
        self._require_position(node_id, "节点编号")
        if node_id not in self.nodes:
            raise ChordError("节点 {} 不在环上".format(node_id))
        if len(self.nodes) == 1:
            del self.nodes[node_id]
            del self.holdings[node_id]
            return node_id
        node = self.nodes[node_id]
        pred = node.predecessor
        succ = node.successor
        self.nodes[pred].successor = succ
        self.nodes[succ].predecessor = pred
        del self.nodes[node_id]
        keys = self.holdings.pop(node_id)
        self.holdings[succ].update(keys)
        self._rebuild_all_fingers()
        self._redistribute()
        return node_id

    def _redistribute(self):
        """环结构变化后，把所有键重新对齐到归属节点与副本节点。"""
        pairs = {}
        for node_id in self.node_ids():
            data = self.holdings[node_id]
            for key_id, value in data.items():
                pairs[key_id] = value
            data.clear()
        for key_id, value in sorted(pairs.items()):
            owner = self.lookup(key_id)
            if owner is None:
                continue
            for holder in self._holder_chain(owner):
                self.holdings[holder][key_id] = value
