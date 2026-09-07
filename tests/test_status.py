from matebot.status import DISCONNECTED, StatusAccumulator


def test_partial_status_and_explicit_clear():
    acc = StatusAccumulator()
    acc.apply({'tp': 'evt:status', 'm': 1, 'p': 'Classic'})
    first = acc.apply({'tp': 'evt:status', 'ct': 92, 'process': {'a': 1, 'e': 30000}})
    state = acc.apply({'tp': 'evt:status', 'p': 'New selection'})
    assert state['process']['a'] == 1
    assert state['ct'] == 92
    last = acc.apply({'tp': 'evt:status', 'process': None})
    assert last['process'] is None
    assert first['process']['a'] == 1
    assert first['p'] == 'Classic'
    first['process']['a'] = 0
    assert state['process']['a'] == 1


def test_process_replaced_events_pass_through_and_reset():
    acc = StatusAccumulator()
    acc.apply({'tp': 'evt:status', 'm': 1, 'process': {'a': 1, 'e': 1000}})
    assert acc.apply({'tp': 'evt:status', 'process': {'a': 0}})['process'] == {'a': 0}
    event = {'tp': 'res:profiles:list', 'rid': 'abc'}
    assert acc.apply(event) == event
    assert acc.apply({'tp': DISCONNECTED}) == {'tp': DISCONNECTED}
    assert acc.apply({'tp': 'evt:status', 'ct': 20}) == {'tp': 'evt:status', 'ct': 20}
