from phonelens.normalize import parse_duration, parse_quantity


def test_duration_hours_minutes():
    assert parse_duration("9小时56分") == 596.0
    assert parse_duration("1小时18分钟") == 78.0
    assert parse_duration("73分钟") == 73.0
    assert parse_duration("9h56m") == 596.0


def test_duration_chinese_and_ge():
    assert parse_duration("九小时56分") == 596.0
    assert parse_duration("十二个小时35分钟") == 755.0
    assert parse_duration("一小时6分钟") == 66.0
    assert parse_duration("九个半小时") == 570.0


def test_quantity_units():
    assert parse_quantity("9070mAh") == (9070.0, "mAh")
    assert parse_quantity("1200nit") == (1200.0, "nit")
    assert parse_quantity("119.9fps") == (119.9, "fps")
    assert parse_quantity("8.45mm") == (8.45, "mm")
    assert parse_quantity("241克") == (241.0, "g")


def test_charge_time_from_text():
    value, unit = parse_quantity("1小时18分钟", "min")
    assert unit == "min"
    assert value == 78.0
