from decimal import Decimal as D

from crypto_robot.futures_agent.agent import AgentConfig, NoStrategy, Position, Snapshot, ewma_vol, size, tick

H = 3600 * 1000
CFG = AgentConfig(capital=D(300), loss_cap=D(300), max_leverage=D(2), target_vol=D(1))


class Want:
    def __init__(self, d):
        self.d = d

    def decide(self, snap):
        return self.d


def snap(pos=Position(), **kw):
    base = dict(now_ms=10 * H, bar_ms=10 * H, price=D(3000), equity=D(300), position=pos, vol=D('0.5'))
    return Snapshot(**(base | kw))


def kinds(acts):
    return [a.kind for a in acts]


def test_no_strategy_never_trades():
    assert kinds(tick(snap(), CFG, NoStrategy())) == ['hold']


def test_open_with_leverage_cap_and_exchange_stop():
    [a] = tick(snap(), CFG, Want(1))
    assert (a.kind, a.side, a.qty, a.stop) == ('open', 1, D('0.200'), D(2850))
    [a] = tick(snap(), CFG, Want(-1))
    assert (a.side, a.stop) == (-1, D(3150))
    assert size(D(1), D(3000), CFG, D('0.5')) == 0


def test_volatility_scales_position():
    assert tick(snap(vol=D(2)), CFG, Want(1))[0].qty == D('0.050')       # 預期很動盪：槓桿 1 ÷ 2 = 0.5 倍
    assert tick(snap(vol=D('0.1')), CFG, Want(1))[0].qty == D('0.200')   # 很平靜也不超過上限 2 倍
    [a] = tick(snap(vol=D(0)), CFG, Want(1))
    assert (a.kind, a.note) == ('hold', '沒有波動預測')
    assert ewma_vol([D('0.01')] * 30) == 0
    calm, wild = [D('0.01'), D('-0.01')] * 20, [D('0.01'), D('-0.01')] * 15 + [D('0.08'), D('-0.08')] * 5
    assert abs(ewma_vol(calm) - D('0.01') * D(365).sqrt()) < D('1e-9')
    assert ewma_vol(wild) > ewma_vol(calm) * 2


def test_flip_closes_first():
    pos = Position(D('0.1'), D(3000), True)
    assert kinds(tick(snap(pos), CFG, Want(-1))) == ['close', 'open']
    assert kinds(tick(snap(pos), CFG, Want(0))) == ['close']


def test_loss_cap_closes_and_stops_forever():
    pos = Position(D('0.1'), D(3000), True)
    assert kinds(tick(snap(pos, equity=D(0)), CFG, Want(1))) == ['close', 'stop_forever']
    assert kinds(tick(snap(equity=D(0)), CFG, Want(1))) == ['stop_forever']
    assert kinds(tick(snap(stopped=True), CFG, Want(1))) == ['halt']


def test_missing_stop_is_fixed_even_when_paused_or_stale():
    pos = Position(D('0.1'), D(3000), False)
    assert kinds(tick(snap(pos, paused=True), CFG, Want(-1))) == ['place_stop', 'hold']
    assert kinds(tick(snap(pos, now_ms=13 * H), CFG, Want(-1))) == ['place_stop', 'hold']
    assert tick(snap(pos), CFG, Want(1))[0].stop == D(2850)
