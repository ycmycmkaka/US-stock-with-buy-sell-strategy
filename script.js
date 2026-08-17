let allRows = [];
let filteredRows = [];
let currentSort = "entry_status";

const summaryCard = document.getElementById("summaryCard");
const rulesCard = document.getElementById("rulesCard");
const searchInput = document.getElementById("searchInput");
const sortSelect = document.getElementById("sortSelect");
const statusFilter = document.getElementById("statusFilter");
const resultsBody = document.getElementById("resultsBody");
const emptyState = document.getElementById("emptyState");
const countText = document.getElementById("countText");


function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}


function isEmptyValue(value) {
  return value === null || value === undefined || value === "";
}


function formatMarketCap(value) {
  if (isEmptyValue(value)) return "-";

  const n = Number(value);
  if (!Number.isFinite(n)) return "-";

  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;

  return `$${(n / 1e6).toFixed(0)}M`;
}


function formatDollarVolume(value) {
  if (isEmptyValue(value)) return "-";

  const n = Number(value);
  if (!Number.isFinite(n)) return "-";

  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;

  return `$${n.toFixed(0)}`;
}


function formatPrice(value) {
  if (isEmptyValue(value)) return "-";

  const n = Number(value);
  return Number.isFinite(n)
    ? `$${n.toFixed(2)}`
    : "-";
}


function formatPct(value) {
  if (isEmptyValue(value)) return "-";

  const n = Number(value);
  if (!Number.isFinite(n)) return "-";

  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}%`;
}


function formatRatio(value) {
  if (isEmptyValue(value)) return "-";

  const n = Number(value);
  return Number.isFinite(n)
    ? `${n.toFixed(2)}×`
    : "-";
}


function pctClass(value) {
  if (isEmptyValue(value)) return "neutral";

  const n = Number(value);
  if (!Number.isFinite(n)) return "neutral";

  if (n > 0) return "positive";
  if (n < 0) return "negative";

  return "neutral";
}


function statusClass(status) {
  const s = String(status || "WATCH").toUpperCase();

  if (s === "READY") return "ready";
  if (s === "WAIT") return "wait";

  return "watch";
}


function contractionsText(values) {
  if (!Array.isArray(values) || !values.length) return "-";

  return values
    .map(v => `${Number(v).toFixed(1)}%`)
    .join(" → ");
}


function renderSummary(data) {
  const stats = data.scan_stats || {};
  const count = Array.isArray(data.results)
    ? data.results.length
    : 0;

  summaryCard.innerHTML = `
    <div class="summary-label">
      今日符合強勢條件
    </div>

    <div class="summary-count">
      ${count} 隻
    </div>

    <div class="summary-updated">
      最後更新：${escapeHtml(data.generated_at || "Unknown")}
    </div>

    <div class="summary-pills">
      <span class="mini ready">
        READY ${Number(stats.entry_ready || 0)}
      </span>

      <span class="mini watch">
        WATCH ${Number(stats.entry_watch || 0)}
      </span>

      <span class="mini wait">
        WAIT ${Number(stats.entry_wait || 0)}
      </span>
    </div>
  `;
}


function renderRules(data) {
  const r = data.rules || {};
  const benchmark = r.benchmark_symbol || "SPY";

  const chips = [
    `市值 ≥ ${formatMarketCap(r.market_cap_min)}`,

    `20D平均成交額 ≥ ${formatDollarVolume(
      r.min_avg_dollar_volume_20d
    )}`,

    `Trend Template = ${
      Number(
        r.trend_template_required_score ?? 8
      )
    }/8`,

    `RS ≥ ${
      Number(
        r.min_rs_rating ?? 80
      ).toFixed(0)
    }`,

    `20D跑贏 ${benchmark} ≥ ${
      Number(
        r.min_rs_20d_vs_spy_pct ?? 0
      ).toFixed(1)
    }%`,

    `60D跑贏 ${benchmark} ≥ ${
      Number(
        r.min_rs_60d_vs_spy_pct ?? 0
      ).toFixed(1)
    }%`,

    `距52週高位 ≤ ${
      Number(
        r.max_dist_from_52w_high_pct ?? 15
      ).toFixed(1)
    }%`,

    `Near Pivot ±${
      Number(
        r.near_pivot_pct ?? 3
      ).toFixed(1)
    }%`,

    `Breakout量 ≥ ${
      Number(
        r.breakout_volume_ratio_min ?? 1.3
      ).toFixed(1)
    }× 50D平均`
  ];

  rulesCard.innerHTML = `
    <div class="rules-title">
      目前篩選與 Entry 規則
    </div>

    <div class="rule-chips">
      ${chips
        .map(
          c =>
            `<span class="rule-chip">${escapeHtml(c)}</span>`
        )
        .join("")}
    </div>

    <div class="rules-extra">
      Pivot 由最近確認 swing highs / resistance cluster 推算；
      VCP 為程式化候選辨識，最後仍建議人眼核對圖形。
    </div>
  `;
}


function sortRows(rows, key) {
  const cloned = [...rows];

  const num = (v, fallback = -Infinity) => {
    if (isEmptyValue(v)) return fallback;

    const n = Number(v);
    return Number.isFinite(n)
      ? n
      : fallback;
  };

  const statusRank = {
    READY: 0,
    WATCH: 1,
    WAIT: 2
  };


  switch (key) {

    case "entry_status":

      cloned.sort(
        (a, b) =>
          (
            statusRank[a.entry_status] ?? 9
          ) -
          (
            statusRank[b.entry_status] ?? 9
          ) ||
          num(b.rs_rating) -
          num(a.rs_rating)
      );

      break;


    case "dist_from_pivot":

      cloned.sort(
        (a, b) =>
          Math.abs(
            num(
              a.dist_from_pivot_pct,
              999
            )
          ) -
          Math.abs(
            num(
              b.dist_from_pivot_pct,
              999
            )
          )
      );

      break;


    case "rs_rating_desc":

      cloned.sort(
        (a, b) =>
          num(b.rs_rating) -
          num(a.rs_rating)
      );

      break;


    case "avg_dollar_volume_desc":

      cloned.sort(
        (a, b) =>
          num(
            b.avg_dollar_volume_20d
          ) -
          num(
            a.avg_dollar_volume_20d
          )
      );

      break;


    case "rs_60d_vs_spy_pct_desc":

      cloned.sort(
        (a, b) =>
          num(
            b.rs_60d_vs_spy_pct
          ) -
          num(
            a.rs_60d_vs_spy_pct
          )
      );

      break;


    case "symbol_asc":

      cloned.sort(
        (a, b) =>
          String(
            a.symbol || ""
          ).localeCompare(
            String(
              b.symbol || ""
            )
          )
      );

      break;
  }

  return cloned;
}


function renderTable(rows) {
  resultsBody.innerHTML = "";

  if (!rows.length) {

    emptyState.classList.remove(
      "hidden"
    );

    countText.textContent =
      "顯示 0 隻";

    return;
  }

  emptyState.classList.add(
    "hidden"
  );

  countText.textContent =
    `顯示 ${rows.length} 隻`;


  resultsBody.innerHTML =
    rows.map(row => {

      const status =
        String(
          row.entry_status ||
          "WATCH"
        ).toUpperCase();


      const details = [

        `Pivot來源：${
          row.pivot_reason || "-"
        }`,

        `10MA ${
          formatPrice(row.ma10)
        } (${
          formatPct(
            row.dist_from_10ma_pct
          )
        })`,

        `20MA ${
          formatPrice(row.ma20)
        } (${
          formatPct(
            row.dist_from_20ma_pct
          )
        })`,

        `50MA ${
          formatPrice(row.ma50)
        } (${
          formatPct(
            row.dist_from_50ma_pct
          )
        })`,

        `Volume dry-up ratio：${
          formatRatio(
            row.volume_dryup_ratio
          )
        }`,

        `VCP Candidate：${
          row.vcp_candidate
            ? "YES"
            : "NO"
        }`

      ].join("\n");


      return `
        <tr title="${escapeHtml(details)}">

          <td class="symbol-cell">
            ${escapeHtml(
              row.symbol || ""
            )}
          </td>

          <td class="company-cell">
            ${escapeHtml(
              row.company || ""
            )}
          </td>

          <td>
            <span class="status ${statusClass(status)}">
              ${escapeHtml(status)}
            </span>
          </td>

          <td class="setup-cell">
            ${escapeHtml(
              row.entry_setup || "-"
            )}
          </td>

          <td>
            ${formatPrice(
              row.recent_close
            )}
          </td>

          <td>
            ${formatPrice(
              row.pivot
            )}
          </td>

          <td class="${pctClass(
            row.dist_from_pivot_pct
          )}">
            ${formatPct(
              row.dist_from_pivot_pct
            )}
          </td>

          <td class="${pctClass(
            row.dist_from_20ma_pct
          )}">
            ${formatPct(
              row.dist_from_20ma_pct
            )}
          </td>

          <td>
            ${formatRatio(
              row.volume_ratio_50d
            )}
          </td>

          <td>
            ${formatDollarVolume(
              row.avg_dollar_volume_20d
            )}
          </td>

          <td class="contraction-cell">
            ${escapeHtml(
              contractionsText(
                row.contractions
              )
            )}
          </td>

          <td>
            <span class="badge-rs">
              ${
                isEmptyValue(
                  row.rs_rating
                )
                  ? "-"
                  : Number(
                      row.rs_rating
                    ).toFixed(0)
              }
            </span>
          </td>

          <td class="${pctClass(
            row.rs_20d_vs_spy_pct
          )}">
            ${formatPct(
              row.rs_20d_vs_spy_pct
            )}
          </td>

          <td class="${pctClass(
            row.rs_60d_vs_spy_pct
          )}">
            ${formatPct(
              row.rs_60d_vs_spy_pct
            )}
          </td>

          <td>
            ${formatMarketCap(
              row.market_cap
            )}
          </td>

        </tr>
      `;

    }).join("");
}


function applySearchAndSort() {

  const keyword =
    (
      searchInput.value || ""
    )
    .trim()
    .toLowerCase();


  const wantedStatus =
    statusFilter.value || "ALL";


  filteredRows =
    allRows.filter(row => {

      const symbol =
        String(
          row.symbol || ""
        ).toLowerCase();


      const company =
        String(
          row.company || ""
        ).toLowerCase();


      const textOk =
        !keyword ||
        symbol.includes(keyword) ||
        company.includes(keyword);


      const statusOk =
        wantedStatus === "ALL" ||
        String(
          row.entry_status ||
          "WATCH"
        ) === wantedStatus;


      return (
        textOk &&
        statusOk
      );
    });


  filteredRows =
    sortRows(
      filteredRows,
      currentSort
    );


  renderTable(
    filteredRows
  );
}


async function loadData() {

  try {

    const response =
      await fetch(
        `results.json?t=${Date.now()}`,
        {
          cache: "no-store"
        }
      );


    if (!response.ok) {
      throw new Error(
        `HTTP ${response.status}`
      );
    }


    const data =
      await response.json();


    renderSummary(data);

    renderRules(data);


    allRows =
      Array.isArray(
        data.results
      )
        ? data.results
        : [];


    applySearchAndSort();


  } catch (error) {

    console.error(error);


    summaryCard.innerHTML = `
      <div class="summary-label">
        載入失敗
      </div>

      <div class="summary-updated">
        未能讀取 results.json
      </div>
    `;


    resultsBody.innerHTML = "";


    emptyState.classList.remove(
      "hidden"
    );


    countText.textContent =
      "顯示 0 隻";
  }
}


searchInput.addEventListener(
  "input",
  applySearchAndSort
);


statusFilter.addEventListener(
  "change",
  applySearchAndSort
);


sortSelect.addEventListener(
  "change",
  () => {

    currentSort =
      sortSelect.value;

    applySearchAndSort();

  }
);


loadData();
