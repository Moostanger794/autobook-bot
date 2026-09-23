from aiogram.fsm.state import State, StatesGroup


class BookingFlow(StatesGroup):
    select_service = State()
    enter_car = State()
    select_date = State()
    select_time = State()
    enter_phone = State()
    enter_comment = State()
    confirm = State()


class AdminSearch(StatesGroup):
    enter_id = State()


class AdminServiceFlow(StatesGroup):
    price = State()
    duration = State()
    add_name = State()
    add_description = State()
    add_price = State()
    add_duration = State()
