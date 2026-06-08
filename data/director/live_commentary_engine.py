import random


class LiveCommentaryEngine:
    """
    STEP 25C
    TV-style live commentary generator
    """

    def generate(self, home, away, live_state, events):

        pressure = live_state.get("pressure", "Low")
        tempo = live_state.get("tempo", "Controlled")
        dominance = live_state.get("dominance", "Even")
        match_state = live_state.get("match_state", "Dynamic match flow")

        event_list = events.get("events", [])

        commentary_blocks = []

        # =====================================================
        # 1. OPENING FLOW
        # =====================================================

        opening = (
            f"Мачът между {home} и {away} продължава с високо напрежение. "
            f"В момента темпото е {tempo.lower()}, а натискът е {pressure.lower()}."
        )

        commentary_blocks.append(opening)

        # =====================================================
        # 2. MOMENTUM BASED COMMENTARY
        # =====================================================

        if dominance == home:
            commentary_blocks.append(
                f"{home} изглежда по-активният отбор и контролира по-голяма част от играта."
            )

        elif dominance == away:
            commentary_blocks.append(
                f"{away} намира ритъм и започва да доминира в ключови зони."
            )

        else:
            commentary_blocks.append(
                "Двата отбора си разменят инициативата и мачът остава напълно балансиран."
            )

        # =====================================================
        # 3. LIVE EVENTS STORYTELLING
        # =====================================================

        for event in event_list[-3:]:  # last 3 events only (broadcast logic)

            if event["type"] == "goal":
                commentary_blocks.append(
                    f"⚽ ГОООЛ! {event['team']} намира мрежата в {event['minute']} минута. "
                    f"Това може да промени целия ход на мача!"
                )

            elif event["type"] == "big_chance":
                commentary_blocks.append(
                    f"🔥 Огромен шанс за {event['team']} в {event['minute']} минута! "
                    f"Вратарят реагира блестящо."
                )

            elif event["type"] == "yellow_card":
                commentary_blocks.append(
                    f"🟨 Жълт картон за {event['team']} след нарушение под напрежение."
                )

            elif event["type"] == "red_card":
                commentary_blocks.append(
                    f"🟥 ЧЕРВЕН КАРТОН! {event['team']} остава с човек по-малко."
                )

            elif event["type"] == "momentum_shift":
                commentary_blocks.append(
                    f"📊 Рязка промяна в инерцията – {event['team']} поема контрол!"
                )

        # =====================================================
        # 4. MATCH STATE COMMENTARY
        # =====================================================

        if match_state == "CRITICAL MOMENTUM SHIFT":
            commentary_blocks.append(
                "🔥 Моментът е критичен – мачът може да се обърне във всяка секунда!"
            )

        elif match_state == "HIGH INTENSITY PHASE":
            commentary_blocks.append(
                "⚡ Висок интензитет в тази фаза на срещата."
            )

        elif match_state == "TACTICAL STALEMATE":
            commentary_blocks.append(
                "🧠 Тактическо надлъгване – малко пространства за атака."
            )

        # =====================================================
        # 5. FINAL BROADCAST LINE
        # =====================================================

        commentary_blocks.append(
            f"Следим развитието на {home} срещу {away} – мач, който може да се реши във всеки момент."
        )

        return {
            "commentary": commentary_blocks,
            "live_state": live_state,
            "event_count": len(event_list)
        }