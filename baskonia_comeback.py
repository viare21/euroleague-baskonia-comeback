import pandas as pd
import os
import matplotlib.pyplot as plt
from statsmodels.stats.proportion import proportions_ztest
from scipy.stats import fisher_exact

# ============================================================
# 0. PATHS (relative to the script, so the project runs on any PC)
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# CSVs live in a "csv_euroleague" folder at the same level as the project
# Adjust this line if your folder structure is different
DATA_DIR = os.path.join(BASE_DIR, "..", "csv_euroleague")

IMAGES_DIR = os.path.join(BASE_DIR, "images")
os.makedirs(IMAGES_DIR, exist_ok=True)

QUARTER_ORDER = {'q1': 1, 'q2': 2, 'q3': 3, 'q4': 4, 'extra_time': 5}
DEFICIT_THRESHOLD = -15  # minimum deficit (at any point in the game) to count as a "big deficit" game

# ============================================================
# 1. LOAD DATA
# ============================================================

header = pd.read_csv(os.path.join(DATA_DIR, "euroleague_header.csv"))
pbp = pd.read_csv(os.path.join(DATA_DIR, "euroleague_play_by_play.csv"))

# ============================================================
# 2. BASKONIA: BUILD GAME-LEVEL DEFICIT / COMEBACK DATA
# ============================================================

bas_games = header[
    (header['team_id_a'] == 'BAS') | (header['team_id_b'] == 'BAS')
][['game_id', 'season_code', 'team_id_a', 'team_id_b', 'score_a', 'score_b']].copy()

print(f"Baskonia games found: {len(bas_games)}")

bas_pbp = pbp[pbp['game_id'].isin(bas_games['game_id'])].copy()

bas_pbp['quarter_order'] = bas_pbp['quarter'].map(QUARTER_ORDER)
bas_pbp = bas_pbp.merge(bas_games[['game_id', 'team_id_a', 'team_id_b']], on='game_id', how='left')
bas_pbp['venue'] = bas_pbp.apply(lambda row: 'Home' if row['team_id_a'] == 'BAS' else 'Away', axis=1)
bas_pbp = bas_pbp.sort_values(['game_id', 'quarter_order', 'number_of_play'])

# Forward-fill scores within each game (non-scoring plays don't update points_a/points_b)
bas_pbp[['points_a', 'points_b']] = bas_pbp.groupby('game_id')[['points_a', 'points_b']].ffill()

# Score deficit from Baskonia's perspective (negative = Baskonia behind)
bas_pbp['bas_deficit'] = bas_pbp.apply(
    lambda row: (row['points_a'] - row['points_b']) if row['team_id_a'] == 'BAS'
                else (row['points_b'] - row['points_a']),
    axis=1
)

def analyze_game(group):
    """For a single game: find the worst deficit, and whether the score was
    tied or better at any point afterward."""
    group = group.reset_index(drop=True)
    worst_idx = group['bas_deficit'].idxmin()
    worst_deficit = group.loc[worst_idx, 'bas_deficit']
    comeback = (group.loc[worst_idx:, 'bas_deficit'] >= 0).any()
    venue = group['venue'].iloc[0]
    return pd.Series({'worst_deficit': worst_deficit, 'comeback': comeback, 'venue': venue})

bas_game_results = bas_pbp.groupby('game_id').apply(analyze_game).reset_index()

bas_big_deficit = bas_game_results[bas_game_results['worst_deficit'] <= DEFICIT_THRESHOLD].copy()

print(f"Baskonia games with a 15+ point deficit at some point: {len(bas_big_deficit)}")

bas_comeback_summary = bas_big_deficit.groupby('venue')['comeback'].agg(['sum', 'count'])
bas_comeback_summary['comeback_rate_pct'] = (bas_comeback_summary['sum'] / bas_comeback_summary['count'] * 100).round(1)

print(bas_comeback_summary)

# ============================================================
# 3. LEAGUE-WIDE: SAME LOGIC, ALL TEAMS
# ============================================================

pbp['quarter_order'] = pbp['quarter'].map(QUARTER_ORDER)
pbp = pbp.sort_values(['game_id', 'quarter_order', 'number_of_play'])
pbp[['points_a', 'points_b']] = pbp.groupby('game_id')[['points_a', 'points_b']].ffill()
pbp[['points_a', 'points_b']] = pbp[['points_a', 'points_b']].fillna(0)

pbp['deficit_home'] = pbp['points_a'] - pbp['points_b']
pbp['deficit_away'] = -pbp['deficit_home']

def analyze_game_full(group):
    """Same logic as analyze_game, but computed for both home and away
    perspectives at once (since team_a is always home, team_b always away)."""
    group = group.reset_index(drop=True)

    idx_min_home = group['deficit_home'].idxmin()
    worst_home = group.loc[idx_min_home, 'deficit_home']
    comeback_home = (group.loc[idx_min_home:, 'deficit_home'] >= 0).any()

    idx_min_away = group['deficit_away'].idxmin()
    worst_away = group.loc[idx_min_away, 'deficit_away']
    comeback_away = (group.loc[idx_min_away:, 'deficit_away'] >= 0).any()

    return pd.Series({
        'worst_deficit_home': worst_home, 'comeback_home': comeback_home,
        'worst_deficit_away': worst_away, 'comeback_away': comeback_away
    })

print("Processing all EuroLeague games — this may take a couple of minutes...")
league_results = pbp.groupby('game_id').apply(analyze_game_full).reset_index()
print("Done.")

# Reshape to long format (Home/Away as rows, not columns)
home_side = league_results[['game_id', 'worst_deficit_home', 'comeback_home']].rename(
    columns={'worst_deficit_home': 'worst_deficit', 'comeback_home': 'comeback'}
)
home_side['venue'] = 'Home'

away_side = league_results[['game_id', 'worst_deficit_away', 'comeback_away']].rename(
    columns={'worst_deficit_away': 'worst_deficit', 'comeback_away': 'comeback'}
)
away_side['venue'] = 'Away'

league_long = pd.concat([home_side, away_side], ignore_index=True)
league_big_deficit = league_long[league_long['worst_deficit'] <= DEFICIT_THRESHOLD].copy()

print(f"League-wide games with 15+ point deficit: {len(league_big_deficit)}")

league_comeback_summary = league_big_deficit.groupby('venue')['comeback'].agg(['sum', 'count'])
league_comeback_summary['comeback_rate_pct'] = (league_comeback_summary['sum'] / league_comeback_summary['count'] * 100).round(1)

print(league_comeback_summary)

# ============================================================
# 4. STATISTICAL SIGNIFICANCE: BASKONIA vs LEAGUE
# (counts pulled directly from the summary tables above, not hardcoded,
# so this stays correct if the underlying data or logic ever changes)
# ============================================================

count_home = [
    bas_comeback_summary.loc['Home', 'sum'],
    league_comeback_summary.loc['Home', 'sum']
]
nobs_home = [
    bas_comeback_summary.loc['Home', 'count'],
    league_comeback_summary.loc['Home', 'count']
]
z_stat_home, p_value_home = proportions_ztest(count_home, nobs_home)

count_away = [
    bas_comeback_summary.loc['Away', 'sum'],
    league_comeback_summary.loc['Away', 'sum']
]
nobs_away = [
    bas_comeback_summary.loc['Away', 'count'],
    league_comeback_summary.loc['Away', 'count']
]
z_stat_away, p_value_away = proportions_ztest(count_away, nobs_away)

print(f"Home — z-test p-value: {p_value_home:.4f}")
print(f"Away — z-test p-value: {p_value_away:.4f}")

table_home = [
    [count_home[0], nobs_home[0] - count_home[0]],
    [count_home[1], nobs_home[1] - count_home[1]]
]
odds_ratio_home, p_value_fisher_home = fisher_exact(table_home)

table_away = [
    [count_away[0], nobs_away[0] - count_away[0]],
    [count_away[1], nobs_away[1] - count_away[1]]
]
odds_ratio_away, p_value_fisher_away = fisher_exact(table_away)

print(f"Home — Fisher odds ratio: {odds_ratio_home:.2f}, p-value: {p_value_fisher_home:.4f}")
print(f"Away — Fisher odds ratio: {odds_ratio_away:.2f}, p-value: {p_value_fisher_away:.4f}")

# ============================================================
# 5. PLOT: BASKONIA vs LEAGUE AVERAGE, COMEBACK RATE
# (values pulled directly from the summary tables above, not hardcoded)
# ============================================================

BG_COLOR = '#0a1f3d'
BASKONIA_COLOR = '#90EE90'
MARKER_COLOR = '#D35400'

categories = ['Home', 'Away']
baskonia_values = [
    bas_comeback_summary.loc['Home', 'comeback_rate_pct'],
    bas_comeback_summary.loc['Away', 'comeback_rate_pct']
]
league_values = [
    league_comeback_summary.loc['Home', 'comeback_rate_pct'],
    league_comeback_summary.loc['Away', 'comeback_rate_pct']
]

x = range(len(categories))
bar_width = 0.5

fig, ax = plt.subplots(figsize=(7.5, 6.5))
fig.patch.set_facecolor(BG_COLOR)
ax.set_facecolor(BG_COLOR)

bars = ax.bar(x, baskonia_values, bar_width, color=BASKONIA_COLOR, label='Baskonia', zorder=2)

for i, league_val in enumerate(league_values):
    ax.hlines(y=league_val, xmin=i - bar_width/2, xmax=i + bar_width/2,
              color=MARKER_COLOR, linewidth=3.5, zorder=3,
              label='EuroLeague avg' if i == 0 else None)

ax.set_xticks(x)
ax.set_xticklabels(categories, color='white')
ax.set_ylabel('Comeback rate (%)', color='white')
ax.set_title('Comeback rate after a 15+ point deficit\nBaskonia vs EuroLeague average', color='white')

ax.tick_params(colors='white')
for spine in ax.spines.values():
    spine.set_color('white')

ax.set_ylim(0, max(baskonia_values) * 1.2)
ax.set_xlim(-0.6, len(categories) - 1 + 0.75)

legend = ax.legend(facecolor=BG_COLOR, edgecolor='white', loc='upper right')
plt.setp(legend.get_texts(), color='white')

for i, (bar, bas_val, league_val) in enumerate(zip(bars, baskonia_values, league_values)):
    ax.text(bar.get_x() + bar.get_width()/2, bas_val + 1, f'{bas_val}%', ha='center', fontweight='bold', color='white')
    ax.text(bar.get_x() + bar.get_width()/2 + 0.32, league_val, f'{league_val}%', va='center', ha='left', fontsize=9, color=MARKER_COLOR, fontweight='bold')

plt.tight_layout()
plt.savefig(os.path.join(IMAGES_DIR, 'baskonia_comeback_rate.png'), dpi=150, facecolor=fig.get_facecolor(), pad_inches=0.3)
plt.show()