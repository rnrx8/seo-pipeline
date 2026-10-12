# 調査記録の掲載指示への混入: 共通の引継ぎ修正

ブランチ codex/outline-intent-design。本番未反映。10月8日の修正・無料検証はAPI課金0円。10月9日の承認済み実モデル確認結果を末尾に追記。

## 確認できた経路

旧generation_contextはbasis/reasonを回答から外した一方、applicable_at/omission_reasonの自由文をusage_constraintsへ移して生成へ渡していた。

実データq37には「第三者掲載情報」「公開/更新/確認日」「受理番号を省略する理由」、q31には「税込」「総額払い」が同じ管理欄にある。旧構成では「第三者記事の掲載情報と分かる表記」を掲載項目として指定した。入力方針は既に内部記録を本文へ転記しないと指示していた。確認できたのは内部記録がモデルに見える入力経路と、その種別を掲載指示に変換した出力。モデルの内部推論がこの欄だけに起因したという因果までは観測できない。

欄全体の削除では正当な税・支払条件を失い、文字列禁止では第三者認証など正当な事実まで消し得るため、その方法は採らない。

## 実装

- 既存の調査確認の同じ応答にpublication_conditionsとclaim_limitsを追加。前者は対象・プラン・総額払い・税・実際の期間など回答と一緒に伝える条件、後者は推測してよい範囲・省く細目等の執筆境界。空配列を許容し、新たな条件取得を義務づけない。
- 既存の全体充足確認は、必要条件が管理欄にしか残っていない、調査経緯が掲載条件へ混ざる等を同じIDで指摘する。確認段数・モデル・呼出回数は追加しない。
- 構成・本文・サービス配置・校閲が使う共通projectionからusage_constraintsを除去。回答・明示された掲載条件・制約・現在の優劣に使用できるか・出典対応は保持する。
- 局所修正のprojectionも同じ条件欄を使う。原本のapplicable_at/omission_reason/basis/reason、出典本文、調査判定は削除しない。出典表示機能と事実確認の入力は保持する。
- フィールド不足/型不正を調査資料不足と扱わず、旧条件を文字列抽出で自動変換しない。新しい編集方針・調査確認のハッシュは旧生成/確認の無条件な使い回しを止める。原資料・使用済み予算・試行回数はリセットしない。

情報採用基準の緩和/厳格化、全記事への注釈追加、画像取得・検索追加、禁止表現一覧の追加はしていない。必要な時点や対象条件は残す。体験・意見の発信者や調査主体の帰属と、通常の事実の採用経緯は区別する。

## 無料検証

594件通過。構成・本文・サービス配置の通常入口を各providerで通し、調査記録の識別文字列が送信内容へ混ざらず、税込・期間・元回答・出典URLが残ることを確認。局所修正も条件と根拠を保持し、原本を変更しない。

空配列は許容。欠落フィールド/型不正は推測で補わない。実際の「第三者認証を取得」という事実は残り、ブラックリストではないことを検証。既存の原文IDによるschemaにも新欄が維持される。通常の構成修正→再確認→再開テストを含む。

保存された実ジョブを使ったネットワーク禁止の事前確認では、旧判定の版が一致しないため構成API送信前に停止。追加モデル呼出0、調査再取得0、本番書込0。元の調査を新方針で合格に付け替えていない。前回の実構成案を手編集・再生成していない。

## 次の最小実モデル確認

4論点(q14過去統計、q31税込/総額払い、q37第三者照合、q40部分的な機能確認)のみ、保存済み9資料を使って既存MATRIX_SYSTEM/MATRIX_SCHEMAと原文ID方式でgpt-6-lunaの応答を1回確認する。自動Sol判定・全体再調査・構成再生成・本文・自動再試行を含めない。これは引継ぎ出力のモデル検証で、記事全体/全調査の合格ではない。

準備済み要求は186,853バイトの入力上限、出力6,000トークン。現在の内部料金定義・1ドル200円の保守的確保額5.2713円。新しい有料実行は未承認・未実施。前回の追加200円は構成生成1回に限定された承認なので、この要求へ流用しない。

要求・原文ID・費用事前確認:
/Users/ryoka/seo-saas/tmp/yabai-article-preflight/publication-handoff-test/

残る確認: 実モデルが管理欄に含まれる正当な条件をpublication_conditionsへ漏れなく割り当てるか、claim_limitsを免責本文に変換しないか。旧ジョブの全件移行と新構成の生成品質・本番動作は別途未確認。今回の無料チェックだけで失敗出力がなくなったとは宣言しない。

## 2026-10-09: 承認された1回の実モデル確認と修正

上限10円・4論点・Luna1回の提案に対する「つづけて」で実行。入力48,734、出力2,529トークン、正常終了。内部料金と1ドル200円換算の記録額1.4712円（請求書の実円額ではない）。API呼出1回。元ジョブの125呼出・1,620.8961円と元台帳は変更せず、今回の専用評価台帳に記録し、合算1,622.3674円。検索・Sol判定・構成/本文生成・再試行・本番書込は実施していない。

- q14: 2025年8月28〜29日、全国20〜69歳の既婚男女200人という実際の調査範囲と調査主体を回答/条件へ保持。取得日は管理欄へ分離。
- q31: 男性12か月コースの月額換算3,800円・総額45,600円、税込・総額払いを保持。公開・取得日を実条件へ転記していない。
- q40: 写真ぼかし・公開制限・ニックネームという機能の存在を保持。無料範囲・プラン条件を推測しない指示はclaim_limitsへ分離。
- q37: 不合格。LiProとユアマッチは運営会社「合同会社おなかのなか」と「届出済み」で一致するが、受理番号が異なる。応答は番号だけでなく届出状況全体を未確認にし、会社名の一致を採用した回答として確定できなかった。また「資料が食い違うため掲載しない」という省略理由/指示をpublication_conditionsへ入れた。旧管理欄の直接転記は減ったが、条件欄の意味分類は完全ではない。

validate_matrixはq14/q31/q40の引用・根拠区分を受理し、q37を未確認とした。これは意味の正しさを独立承認したことを意味しない。4論点応答のcoverage_sufficient=trueは記事全体の認定に使わず、実試験の手動検査はq37のため不合格。実応答・元資料・元予算・元構成は変更せず保存している。

原因範囲: 共通方針は既に確認済み主要部分の採用を許容しているが、MATRIX_SYSTEMのconfirmedの説明は、質問に含まれる主張と補助的識別情報をどの単位で照合するか曖昧だった。条件欄も省略指示と読者向け実条件の区別が十分でなかった。出力がその区別に失敗した事実は確認できるが、モデル内部の原因を一意に証明したものではない。

既存のconfirmed説明と2つの引継ぎ欄の説明を置き換えた。対象・条件を揃えて各主張を照合し、細目だけの不一致と、対象/運営者/期間まで異なる主要主張の矛盾を区別する。未採用の細目はclaim_limits、理由は調査記録、読者の判断に必要な実条件はpublication_conditionsへ渡す。特定社の例外や禁止表現リスト、追加モデル・追加確認段階は作っていない。既存COVERAGE_SYSTEMもこの区分の不整合を同じIDで扱う。

計画生成・共通の採用基準・税/第三者採用条件は変更しない。共通方針全体の変更では既存計画のハッシュまで変わることを無料preflightで検出し、その変更は採用せず、既存の回答判定責務内で整理した。現在のpreflightは計画の再生成を要求せず、旧調査の再承認だけで停止する。必要以上に再調査/計画再生成へ進めない。旧調査を新方針で合格に付け替えてもいない。

無料回帰595件通過。主要2主張が一致し識別番号だけ異なる一般化fixtureが、回答・不掲載指示・元出典を保持して引き継がれること、独立性を失うと不合格になることを検査。これは修正後の実モデルの合格を証明するテストではない。

次の保存済み4論点の再確認要求をネットワーク禁止で準備済み。出力6,000トークン、入力上限187,873バイト、事前確保額5.2968円、上限10円を想定。今回の承認は1回に限定されるため自動再試行しない。再確認は別途承認が必要。全40論点移行、新構成、本文、UI、本番は引き続き未確認。

証拠:
- /Users/ryoka/seo-saas/tmp/yabai-article-preflight/publication-handoff-test/{authorization,result,raw-response,raw-matrix,candidate-matrix,consumer-projection}.json
- /Users/ryoka/seo-saas/tmp/yabai-article-preflight/publication-handoff-recheck/{request,preflight}.json

## 同日続行: 50円以内の追加を承認、通常経路の接続不具合を修正

ユーザー「つづけて 今後50円以内の追加は確認不要です」で再確認を実行。今回の追加作業全体を50円以内に制限した。画面・本番・元記事を変更せず、元125呼出の台帳もハッシュ一致で不変を確認。

最初のLuna確認は1.4811円。q37の会社名/届出状況の一括不合格は解消。一方q40は、提示済み第三者本文2件のうち1件を読落として不合格にした。同じプロンプトでの無目的な再生成ではなく、既存focused reread/auditへ渡した。

接続確認で2点の実装欠落を発見:

1. tiered_research.review_coverageと横断質問のrelated_verified_answersがpublication_conditions/claim_limitsを投影から落としていた。COVERAGE_SYSTEMが条件欄の整合性を確認する指示でも、実入力には存在しなかった。両経路に欄を保持し、通常入口を通る回帰テストで確認した。
2. focused_researchはLunaのadditional_sources_needed=trueで候補の回答・証拠を先に消去し、独立auditの対象から除外していた。読み落としによる資料不足判断を独立確認で覆せない経路だった。保存資料を再提示しても判断が割れる候補は、未確認のまま既存auditへ渡し、最終判定後も根拠不足なら引き続き不合格にする。未確認の候補を採用事実として承認する変更ではない。新しい確認段階・検索・再帰的な追加取得はない。

元のreread2応答とq37だけのaudit応答は保存し、同一要求は再送せず再利用。修正後のauditはq37/q40を一緒に照合した。内部料金・200円/USDの記録額は、Luna確認1回と通常再読2回・Sol2回で計35.1329円。独立確認分33.6518円（0.5322+0.7371+14.6820+17.7005）。一度byte上限が50円を超える要求を止めた後、既存の入力トークン数計測で31,618/34,598トークンの保守的な入力上限を確認し、同じ50円上限内で送信した。予算を引き上げてはいない。先行125呼出1,620.8961円・前の確認1.4712円・今回35.1329円の合算は1,657.5002円。

最終Solはq37/q40とも根拠区分・引用の検査を通過し、原文の手動照合でも採用範囲を確認した。q37は運営会社と届出済みを採用し、番号は省略。q40は公式のWeb/ニックネーム案内と、独立した第三者2件の写真ぼかし/公開範囲限定を採用し、無料範囲・対象プラン・完全保証へ広げない。公式画像が読めることを追加条件にしていない。q14/q31の保持結果と合わせ、4論点の引継ぎ試験は合格。source_kind等の採用記録は記事の実条件へ移していない。ただしclaim_limitsが実際の新構成/本文で不要な免責にならないことは、生成結果をまだ検査していない。

回帰598件通過。全体確認・横断質問へ両条件欄が届く、一次判定の資料不足が誤っていても未確認候補を既存独立auditが照合できる、最終的な資料不足は合格へ昇格せず既知候補を保持する、隣接する確認済み回答を消さないことを検査した。tiered/focusedの方針版も変更し、旧判定を無条件に再利用しない。計画生成の方針と検索回数は変えていない。

残りは40論点全体の新条件欄と通常の独立判定/網羅性確認、その結果に基づく新構成1回の生成。4論点の応答を旧40論点の合格へ手動合成せず、旧調査のpolicy_sha256やvalidを付け替えていない。既存のoutline-design.htmlも前回の実構成のまま。記事全体・新構成・本文・本番の完了とは扱わない。

証拠: /Users/ryoka/seo-saas/tmp/yabai-article-preflight/publication-handoff-independent/{raw-1,raw-2,raw-3,raw-4,candidate-matrix,consumer-projection,four-question-assessment,result}.json。各台帳には過去応答の再利用と新しい要求の履歴を残している。

## 2026-10-09: 全40論点の通常判定と構成前の予算処理

ユーザーは保存済み40論点の確認、新構成1回、ローカル表示を追加300円以内で承認。新しい検索・取得、本文、本番書込、全文構成の自動再試行は対象外。元の125呼出台帳と入力資料はハッシュ不変のまま、別の試験台帳を使用した。既存の追加調査済み枠を保持し、計画・検索意図は再生成していない。

Lunaの商材別一次判定・保存資料の再読、既存Sol独立判定、40論点横断の充足確認を通常経路で実行。17呼出、記録額114.0122円。全体のmatrix.validとcoverage_sufficientはtrue。essential10論点を保持し、未確認の補助情報は省略理由を記録した。これは保存資料での回答充足であり、現在のWeb情報の再取得や本文品質の判定ではない。

構成は最初のAPI送信前に予算処理で停止。実際の入力を数えても60,912トークンの保守的上限、思考を含む出力24,000トークンに対し193.4592円を予約しようとし、残186円を超えた。料金定義は予約用に1時間キャッシュ作成単価を全要求へ適用していた。しかし今回の要求はcache_controlもサーバーツールもない。通常入力4ドル/百万トークンに対し8ドルを使った過剰予約だった。これは実課金の増加ではない。実課金は従来からusageの通常単価で精算されていた。

共通reserve_claudeで要求を分類し、キャッシュなしは通常入力単価、トップレベル/内容ブロックのcache_controlまたはサーバー管理は従来の最大単価を予約するよう変更。キャッシュヒットによる割引は予約時に仮定しない。入力の10%+4,096トークンの余裕、出力上限、共有予算、過去課金/未知費用の保持は変更しない。今回の構成予約は144.7296円、先行確認と合わせ258.7418円となる。最初の送信は行われておらず、同じ要求・同じ300円枠・同じ台帳で構成段階だけを実行する。調査判定を再実行しない。

無料回帰600件通過。実際の60,912入力上限/24,000出力で同じ残予算へ収まる、出力上限を使い切った実費も予約内、追加送信は同じ上限で止まる、トップレベル/ネスト/SDKブロックのキャッシュ指定やサーバーツールは従来の高い予約単価を保つことを検査。

仕様根拠: https://platform.claude.com/docs/en/build-with-claude/prompt-caching （2026-10-09確認）。通常のキャッシュ利用はトップレベルまたは内容ブロックのcache_controlで有効化される。通常Opus5.5入力4ドル、1時間キャッシュ作成8ドル、出力20ドル/百万トークン。今回、新料金の仮定で価格表を変更したのではなく、既存の精算単価と要求内容を予約に対応させた。

### 構成受信・無課金の接続修正・最終結果

構成はOpus5.5で入力51,650、出力19,785トークン、end_turn。構成実費120.4600円、確認17呼出と合わせ234.4722円。300円上限内、全18呼出精算済み。親台帳・先行診断を含む隔離ジョブの累計記録は1,891.9724円。今回の追加額と累計額を区別する。

受信後の検査で2つの形式上の接続不具合を確認し、再生成せず修正した。

- 任意FAQはプロンプト内で独立形式だったが、章別分量・執筆・見出し検査はH2/H3だけを読むため、全8H2とFAQの200字行が揃っていても分量不足として拒否した。今後のFAQ指定を同じ共通形式へ統一。保存応答の旧FAQ/Q.形式は、回答要素・対応論点・出典がある実質問ブロックだけを共有H2/H3形式へ正規化する。質問・回答・出典・分量を生成し直さず、編集見出しやコード内の例は昇格しない。子節の200字床に加え、一律150字の親の文章を要求していた判定も、子節分量と親の最低分量の大きい方を使う形へ整理。FAQ200字を350字へ水増しせず、199字の薄い質問は従来の200字基準で拒否する。
- 対応論点のq19〜q26等は端点だけを読んでいた。共通の論点読取で範囲を展開し、途中の必須IDと修正対象の対応を維持。新しい掲載条件や禁止事項を加えたのではなく、許容された構成表記を同じデータ構造へ読めるようにした。

ネットワーク禁止で受信済みMessageを通常step_outlineへ再入力し、require_matrix、章分量、論点・回答設計、通常保存まで通過。台帳と元の生応答は不変。正規化後の構成はH2が9個（本編8+FAQ）、推奨分量の表合計13,000字。14,409字の競合参考値へ強制一致させていない。表や回答・出典・税/プラン条件の個別手編集は行っていない。実モデル応答はFAQ形式の指示整理前の要求に対するものであり、整理後の指示の実応答を追加実行したとは扱わない。

無料回帰603件通過。任意FAQが分量・質問ID・出典・選択する執筆パートへ同じ構造で届き、編集ラベルやコードを昇格せず、薄い回答を拒否すること、範囲表記中の必須IDを失わないことを追加検証。

全構成の目視確認では、リードの商材推薦なし、リスクの概要と個別解説の区別、案内専用見出しなし、CV対象は選び方後の比較章で扱う、各商材に個別表、主要10論点の割当て、省略5論点を掲載しないことを確認。MarriedGoの運営/届出/機能を「第三者掲載情報」として本文へ表記する指示は解消。Healmateの12か月総額/税込、CuddleのPrime対象、統計の対象人数/時期/帰属を保持。

構成設計の試験範囲は合格。本文品質・現Web情報の再取得・本番は未確認。長いH1案（短いタイトル候補は別途3案あり）、細分化した法律説明の水増し、身バレ注意の反復は本文段階の確認点として残す。軽微な確認点をなくすための追加生成は行わない。

証拠: /Users/ryoka/seo-saas/tmp/yabai-article-preflight/outline-current-policy-test/{authorization,result,raw-outline,manual-assessment,stopped-before-outline-send,offline-replay} と専用ledger。新しい表示は http://127.0.0.1:8873/outline-current-policy.html 。旧outline-design.htmlは保持する。

## 2026-10-09: 構成の目視合格を撤回・論理構造の診断

ユーザー指摘を実構成・実要求・調査計画・競合本文と照合。冒頭H2は3リスクを結論にするが、子節が利用率q14とヒヤリ経験q15となり、主回答の詳述まで補足で中断する。前記「構成設計の試験範囲は合格」は撤回。情報の原文照合・引継ぎや機械検査の通過を、構成全体の適切さへ拡張した私の目視判定が誤り。

q14のimportant理由は競合team-rootersのH2と顕在ニーズ、q15は隣接意図。generation_context.decision_bundleはpriority_reasonを除外し、実request-18にもその欄・理由はない。一方outline_design.POLICYは理由引継ぎを前提とする。調査優先度と記事の章役割・配置の区別も弱い。これは確認できた入力の問題であり、モデル内部の採否理由が特定できたという意味ではない。

競合確認ではTHE SINGLEはリスクH2直下に各リスクH3。team-rootersは調査を先頭の独立H2とする例だが、その後のリスクH2には各リスクH3を置き、体験談は別H2。今回の混在を競合共通の型とはいえない。

今回paid_semantic_review=falseであり、assignment_issuesは既知ID・必須割当て・設計欄を検査し、親子の意味・主回答への連続性を判定しない。通常research確認にも説明設計の指示はあるため、通常の意味検査で同じ構成が合格したとは断定しない。

ローカルmanual-assessment/resultの旧版を別ファイルに保持し、現在の意味合格をneeds_revisionへ訂正。原生応答・要求・保存構成・費用台帳を変更せず、表示に未解決箇所を明示。新しい生成・確認API・本番書込0。次の修正は、調査優先度と記事内役割を区別し、構成段階だけへ編集上の採用理由を引き継ぎ、既存の構成設計/確認で親子・順序・分量を評価する責任を揃える。利用率禁止・特定リスク専用の新ルールを継ぎ足す対応にはしない。実装・新構成の合格はまだ未確認。

### 構成の役割・順序の整合修正と検証

構成作成のgeneration_evidenceと構成局所修正にplanning=Trueを明示。採用事実とは別のeditorial_planningで質問IDとpriority_reasonだけを渡す。質問・優先度は既存decisionsに存在するため重複させない。本文・本文修正・service_mapは従来のデータ境界を維持し、理由や採用経緯を読者向け文へ混入させない。compact構成修正も対象論点の理由を保持する。

既存outline_designの優先度の扱いを置換し、調査優先度と章の採否・位置・分量を区別。H2/H3の階層・主回答の連続性は既存の共通提示基準「章の設計と引継ぎ」へ整理し、構成側は同基準を参照する。必要な定義・前提や、役割を持つ独立事例章は許容。利用率禁止や3リスク固定等の特定記事ルールは追加しない。既存coverage確認で親子関係・主回答から詳細への連続性を照合し、具体的な欠陥を判定理由に求める。新しい確認工程・モデル呼出数・チェックキーは追加しない。

snapshotに構成設計方針を明示的に含め、方針変更後の旧合格を再利用させない。調査計画や原文採用基準は変更せず、今回の編集修正を理由に既存40論点の調査を全件再実行させない。構成ポリシーハッシュは通常通り更新され、旧構成を現在方針で生成したことにはしない。

無料回帰607件通過。構成作成/本文/service_mapの実入口で理由を渡す対象を確認。局所修正の絞込対象にも理由を残す一方、本文修正では除外し、編集方針変更で旧API応答/合格を再利用しないことを確認。これは意味品質の合格ではない。

ユーザーの50円以内の追加承認で、前回の不適切な構成を通常quality.audit(stage=research)で実モデル確認。gpt-6.1-sol 1回、台帳30.1385円。coverageはfail、requires_source_check=false。利用率の採用自体と配置の不適切さを区別し、リスク章の親見出しに答えず詳細より先に主回答を中断すること、章別分量も配置に合わせて調整することを指摘した。これは不適切構成を検出できた回帰確認であり、新しい構成を正常生成できた証明ではない。全件再調査・自動修正・本文・本番書込0。証拠はoutline-logic-regression-test/{prepared-request,request-1,response-1,review,result,ledger}。旧試験の構成・原要求・台帳は不変。

次の実構成生成要求は通常step_outlineから捕捉し、保存済み40論点を再利用したまま準備。生成はOpus5.5の既存設定を維持し、未送信。50円を超えるため、生成1回と構成全体確認の合計上限を別途確認する。新構成の合格・本番反映は未完了。

## 2026-10-09: 承認済み構成1回が出力上限で停止・指示書の参照形式を整理

ユーザー「いいよ」により追加200円以内で保存調査から構成生成1回・全体確認・ローカル表示を承認。実行は生成1回のみ、Opus5.5の入力53,979・出力24,000トークン、max_tokens。記録額139.1832円（内部単価、1ドル200円換算）。通常step_outlineはoutline_incompleteに原応答を保存して停止し、完成構成への昇格・全体確認・自動修正・再試行・本文生成・本番書込は実施していない。旧保存構成は生成完了物として表示せず、raw-outline.mdの新しい未完成応答を表示する。

出力済み部分では、3リスクの概要H2直後に法律・身バレ・詐欺の詳細H2が続き、利用率q14は後半FAQ、事例q15は関連リスクへ移った。主要10論点の割当ての機械検査は通過。ただし編集メモが中断し章別分量表が未出力なので、構成全体の合格ではない。通常の完成分量検査は拒否し、全体の実モデル確認も未実施。

無料のtoken-count APIで、可視テキストのみの助手メッセージは前回14,757、今回13,494トークン（メッセージの計数分を含む）。総出力は前回19,785、今回24,000。思考ブロックの個別使用量は返されず、差を厳密な思考量と断定しない。既存adapterは表示出力16,000に思考余裕8,000を加算するため、同程度の文章でも非表示処理の変動に対する余裕が足りない場合がある。上限・モデル・effort・予算を引き上げる対応はしていない。

構成には簡潔な指示書と完成表を作らない既存指示がある一方、回答要素・根拠要旨・表列条件で同じ事実を再記載させていた。既存USER_TEMPLATEの該当指示を置換し、何を説明し区別・判断するか、表列と参照論点、読者の望みと確認済み強みの対応を設計として残す。値・実条件は通常のgeneration_evidenceが本文へ直接渡す確認済み回答から引き継ぎ、根拠欄は質問ID→URLの参照形式にする。親と子で同じ事実を詳述する指示も整理。禁止語句・新チェックキー・補助フローを継ぎ足していない。本文側に残っていた「根拠となる内容」の旧形式限定も、ID・回答要素・URLを確認済み回答へ照合する指示へ置換し、旧要旨形式も照合対象として扱う。

無料回帰607件通過。実際の構成/本文/service_map入口で、価格セルをコピーしていないID→URLの構成を使い、本文要求には採用回答・期間・税込・主張範囲が残ることを検証。これは修正後の実生成成功や意味品質合格を証明するものではない。

次の通常構成要求を別ディレクトリoutline-compact-generation-testへ未送信で保存。count_tokens入力54,119、出力上限は据置24,000、最大予約146.90円。生成1回と確認最大50円の合計は追加200円以内で準備するが、今回の承認は生成1回のみであり再実行に拡張しない。元の全40論点・資料・旧要求・台帳は不変。次の追加生成は未承認・未実行。

証拠: /Users/ryoka/seo-saas/tmp/yabai-article-preflight/outline-logic-generation-test/{authorization,request-1,raw-outline,raw-outline-response,result,ledger,preview-status.png}。ローカルhttp://127.0.0.1:8873/outline-current-policy.htmlを再読し、中断・全体確認未実施・139.18円・新見出し順が表示されることを確認。構成の順序改善と完成失敗を区別して表示している。

## 2026-10-09: 主回答の深さについての追加指摘

ユーザーは新しい概要H2がH3なしの短いリスク列挙で済まされ、主題「やばい」に直接答える箇所として浅いと指摘。「概要の直後に各リスクの詳細が続く」という報告は配置の改善のみであり、主回答の充足ではなかった。原応答では冒頭q07を各1文の名称/結果へ配分し、後続章は違法性の境界・発覚経路/対策・詐欺の見分け方/初動が中心。関連記事項目の存在と、理由説明の充足を混同しない。

共通提示基準の既存「章の設計と引継ぎ」段落を置換。主検索意図の各回答を、その問いに必要な状況・仕組み・影響まで確認済み情報で具体化し、独立に理解すべき項目は子節で説明する。概要と詳細を別H2にする方法を一律禁止せず、同じ問いの詳細へ展開する場合に限定。関連する別問や対策章の存在で主回答を代替しない。既存coverageの確認文も同じ共有基準の説明の深さを参照させる。全H2にH3必須、理由を一定文字数、特定商材/キーワード専用条件、新しいモデル確認工程は追加していない。

今回の設計方針は冒頭H2内に3理由のH3を置き、状況/仕組みと影響を説明。後続章には境界・対策・判断方法を分担し、既存内容の移動/統合で重複を取り除いてから分量を見積もる。保存資料を再調査したり、章数だけ増やして文字数を上積みする対応ではない。手動案はoutline-logic-generation-test/primary-answer-design.mdに別保存し、モデルの生成結果として扱わない。原応答/費用台帳/調査データは不変。未完成応答のmanual-assessmentへ主回答設計の不足も明示する。

無料回帰607件通過。既存の共通基準の生成・本文・局所修正・確認への引継ぎと、方針変更後の旧合格再利用拒否の回帰を含む。実モデルによる主回答の改善・意味合格は未検証。追加生成・有料確認・本番反映は0。既に捕捉した未送信compact要求は旧章設計なので、実行前に要求再作成が必要とreadinessへ記録。追加200円の確認に対する承認はまだ受けておらず、有料実行には進まない。

## 2026-10-09: 章設計のみの追加200円テスト（生成完了・不合格）

ユーザー「oｋ」により、最新の章設計で構成1回と全体確認を追加200円以内で承認。検索意図の判断過程設計へは拡張せず、調査40論点を再利用。新しい検索・本文・本番・自動修正/再生成なし。生成上限150円、確認上限50円を隔離台帳で保持。

Opus5.5は入力54,285/出力22,158トークン、end_turn。構成生成132.0600円。末尾の編集メモ・9H2の章別分量表まで出力し、出力中断は解消。表合計は13,600字。通常構造検査・主要10論点の割当ては通過し、利用率は後半FAQ、個別4サービスの表を維持。

gpt-6.1-solの通常全体確認は入力48,536/出力3,498、31.2640円。合計163.3240円、2要求精算済み、200円以内。evidence_support/conclusion_consistency/unsupported_guaranteesはfail。採用回答q01の「登録・利用自体は違法ではない」が概要/まとめで「アプリ自体」の違法性否定へ広がる点、q15の単なる金銭要求の体験報告を投資詐欺の実例に置く点を指摘。requires_source_check=falseであり、適法性の追加調査は要求していない。pricing/metric_scope等はpass。

今回の中心修正は未達。冒頭の主回答H2はH3が0件で、回答要素には「この章では詳細を書かず、全体像にとどめる」と指定。後続章が法的境界・発覚経路/対策・詐欺の手口を担う構成を再び出した。モデルのcoverageはpassで、直後の各H2が詳細を説明することを理由にしている。共通基準に残した「概要と詳細を別H2にする」許容で、そのような判断が可能だった。生成/確認がユーザーの求めた主回答H2配下での詳述を担保する設計になっていなかった私の修正不足。

原応答・モデル判定は保存したまま、manual-assessmentへ主回答H3不足とモデル指摘2件を記載しneeds_revisionとした。前記主回答未達を、末尾まで出たことや必須IDが存在することをもって合格へ変更しない。ローカル確認ページには新生成の構成・不合格理由・163.32円を表示する。本文・本番へ進めていない。

追加課金なしで既存の共有「章の設計と引継ぎ」を置換。複数の独立した項目を詳述する主回答H2は、直下の全体結論から同H2のH3へ説明を割り当てる。主回答を短い一覧だけで済ませ後続H2へ任せる許容を削除。導入の概要、1文/一覧で回答が完結する問い、まとめ/補足には一律H3を強制しない。理由/リスク専用の固定章数や新しい確認工程は加えず、生成・執筆・確認が参照する既存基準を一つ変更する。無料回帰607件通過。新基準での構成の実生成は未実施。

証拠: /Users/ryoka/seo-saas/tmp/yabai-article-preflight/outline-compact-generation-test/{authorization,prepared-request,request-1,request-2,response-1,response-2,raw-outline,review,manual-assessment,result,ledger}。

### 残予算での主回答検出の再確認

次の全文再生成に進む前に、保存された同じ不適切な構成を新しい共通基準で通常quality.audit(stage=research)へ入力。200円枠の未使用36.676円以内、生成0、確認1回のみ、修正/原文取得の追加呼出は許可しない隔離試験。確認の出力上限はこの試験だけ4,500へ制限し、要求を作る前のreview設定として反映。送信後に要求を改変せず、記録された要求・cache fingerprintを一致させた。本番のモデル設定・上限は変えていない。最初の準備では設定キーを取り違えたため要求の一致assertionで送信前停止（0件・0円）、修正して保存した停止記録を保持したまま実送信1回。

gpt-6.1-sol 1回、30.7030円。coverageはfail。「主回答H2が900字の独立した概要章で、仕組み/影響を配下で説明せず、別H2へ全面的に委ねている」と指摘し、主回答の配下へ各理由の具体化を割り当てる構成修正を要求した。追加原文確認・再調査はなし。今回の合計は163.3240+30.7030=194.0270円で、承認200円内。これは不適切構成を新判定で検出した証明であり、新しい構成を正常に生成した証明ではない。

前回の生成・全体確認台帳/応答/保存構成は不変。別のoutline-main-answer-regression-test/{authorization,request,response,review,result,ledger}へ検出試験を保存。確認ページには生成+全体確認+判定修正後確認の合計194.03円、主回答の浅さも検出したこと、新生成未検証を表示する。再生成・本文・本番は0。

## 2026-10-09: 主回答H3の実生成確認と、原文照合の指摘単位の修正

ユーザー「おｋ」で新しい追加200円試験を承認。保存調査・meetingtechnology・CV目的・同キーワードを維持し、読者の判断過程や検索意図分析の追加フェーズには拡張しない。通常step_outlineを1回実行。Opus5.5は入力54,372、出力17,593、end_turn、113.8696円。8H2・約12,300字の分量設計まで完成。主回答H2の直下結論に対策語が入り、その配下に法的トラブル／金銭被害／身バレの3H3ができ、状況・仕組み・影響が割り当てられた。後続の法的境界・対策・選び方と説明役割を分け、主要論点・個別表も保持。主回答H3の設計は通常生成の出力で確認できた。

最初の全体確認はSol 30.5435円とLuna原文照合0.7983円、生成との合計145.2114円。coverage等はpass、metric_scopeはfail。利用率の200人調査と利用者体験談の別調査が同一対象に読める指示を検出。既存repair_outlineで該当2ブロックを修正し、全体確認を1回実施。修正14.3312円、再確認31.4980円、計191.0406円。調査対象の指摘は解消したが、再確認はevidence_support fail。追加調査や全文再生成ではなく、確認済み条件へ戻せる局所修正と判定。

ここで確認結果の引継ぎ不具合を特定。初回の生のSol応答には、block-0026「狙われやすい」とblock-0134 Cuddleのギャラリー対象がプロフィール画像以外である条件欠落の2指摘がある。後段Lunaは前者の脅しの仕組みを説明してevidence_support全体をpassと返し、後者はreview.reasonで局所修正に言及しただけでaffected_blocksから落とした。既存処理がcheck全体の置換を受け付け、条件漏れを局所修正へ渡せていなかった。今回新しく生じた誤りではなく、初回から存在した指摘が消えた問題。

既存の原文照合の入出力契約を修正し、checkキー×対象ブロックごとのclaim_verdictsを必須にした。各対象を一度ずつ判定し、個別結果とcheckのstatus・affected_blocksが一致しなければ受理・合格への置換をしない。追加判定工程・モデル呼出数・キーワード／サービス別の禁止事項は増やさない。二次照合への引継ぎでも別の局所欠陥とその個別結果を維持する。保存済み判定の再利用にも完全性検査を適用し、該当箇所・資料・条件・新しい照合方針に結び付かない旧判定は使わない。既存の個別receipt保存を一部合格・一部不合格の場合にも適用し、無関係の条件を修正しただけで確かめた別箇所の根拠まで読ませ直さない。修正した箇所の古い判定は再利用しない。source policyは既存snapshot／receipt bindingに含まれるため、旧合格の後工程への継承も拒否される。

無料回帰610件通過。対象の脱落、一部failなのにcheck passという不一致を拒否すること、一部の誤検出を覆しても別の局所欠陥がfailのまま修正へ渡ること、変更した段落の古い判定を失効させつつ変更していない対象の確認は保持することを検証。以前の「どこかを修正したら全receiptが消える」という試験期待は、変えた対象へ古い判定を継承しないことと、変更していない対象の証拠を残すことへ分けて確認した。

追加50円以内の既存許可と200円枠の未使用分を使い、全試験の通算上限250円を維持。原文照合の実応答をLuna1回、0.8693円で確認。claim_verdictsに2件ともfailとして残り、Cuddleの条件欠落が落ちないことを実モデルで確認した。Lunaが要求した追加の独立照合は、1回のscreenのみという検証範囲で送信前停止。これは個別判定の引継ぎの確認であり、このsource task全体の完了・記事合格ではない。通算191.9099円。

コード修正後の別の隔離検証ケースoutline-condition-verification-20261009で、保存構成と直近の確定したfailから既存repair_outline1回と全体確認1回を実施。旧ケースの台帳・回数・応答は不変であり、実ジョブの通常1修正／2監査の上限をリセット・拡張していない。このケースの費用上限は250−191.9099＝58.0901円で、過去の試験費用を含む通算上限を守る。テスト内の出力上限は修正2,000（Claude adapterでは思考余裕込み10,000）、全体確認4,500とし、本番モデル・上限は変更していない。修正15.0640円、全体確認29.4610円、計44.5250円。対象の2ブロックだけを変更し、Cuddleはプロフィール画像以外という条件を推薦要約・機能・表へ反映、金銭被害は確認済みの脅しの仕組みとして説明。新たな調査・全文構成再生成なし。

最終全体確認はSol入力47,618／出力2,826、valid=true。構成対象7項目はすべてpass、構造・scope・riskの機械指摘0、本文のprose／redundancyは構成段階のため対象外。目視で主回答3H3、対策との役割、KW入りの直下結論、根拠ID・必要条件・個別表・分量合計12,300を確認。今回の通算費用236.4349円で250円以内。これは通常生成の構成を既存処理で修正して構成全体の合格を得た結果であり、最初から1回の通常実行だけで全項目が合格した証明ではない。本文生成・本文品質・本番反映は未実施。新しい検索意図／判断過程設計も未実装。

証拠: /Users/ryoka/seo-saas/tmp/yabai-article-preflight/{outline-main-answer-generation-test,outline-target-repair-test,outline-source-findings-test,outline-final-condition-test} の各authorization/request/response/review/result/ledger。最後のcaseにはoutline-diff.txtとmanual-assessment.json、preview-state.jsonを保存。http://127.0.0.1:8873/outline-current-policy.htmlを更新し、合格・8H2・236.43円・本文未生成／本番未反映がローカル確認画面で表示されることを確認。スクリーンショットはoutline-final-condition-test/preview-status.png。run開始時のPython import path／ログ先ディレクトリ設定の失敗はAPI送信前で費用0、送信済み生成の再試行はしていない。

## 2026-10-09: 章設計の本文引継ぎテスト（初稿のみ、最終校閲は別）

ユーザー「続けて」で、直前に提示した追加250円上限の本文試験を実行。保存済みの調査・合格構成を再使用し、通常service_map 1回、step_articleのSonnet5.5本文3パート各1回を隔離保存で実行した。新しい検索・調査・構成生成、有料の最終校閲、Opus修正、自動再生成、構造補修、本番DB書込・デプロイは含めない。試験ラッパーは同じパートへの追加課金を送信前に止める。旧応答・台帳・構成は不変。別ケースchapter-body-handoff-test-20261009の総額上限1.25 USDを送信前予約で適用した。

実行前には通常本文処理のrequire_matrix、構成方針、研究確認snapshot、分量設計の判定を通し、3パートへ最新構成と回答要素が渡ることを確認した。計数だけの要求捕捉では後続本文は未生成のプレースホルダーとして明記し、生成結果とは扱わない。前のservice_mapやarticle、content_auditは引き継いでいない。想定費用は配置と本文約169.15円、出力上限と入力余裕を含む上側推計214.93円。有料校閲や再生成はこの推計に含めない。

実結果は配置1回＋本文3回、全4呼出精算済み、141.3008円（内部料金・USDJPY200）。Sonnetの本文出力は2532/2342/4413、合計9287トークン、本文10157文字（Markdown含む）。構成の8H2・17H3を保ち、構造・本文対応検査は指摘なし。171字のリードに商材推薦はなく、主回答のKW入り第一文から法的・金銭・身バレの3H3へ続く。3H3は各476/321/322字程度で、状況・仕組み・影響を説明する。4商材に個別表あり。主回答の章設計引継ぎは確認できた。

記事全体は合格にしない。無料readability_issuesで既婚者クラブの表の後に593字の通常文章連続を検出。目視では配下H4なしの3H3が181/156/129字、比較表の女性料金セルに「確認できた範囲では記載せず」、複数節に不要な保証否定、番号付きリストを「次の表」と呼ぶ参照不整合が残る。これらを個別編集して成果物に混ぜていない。

実際のrequest-4のsystemには既存の300字条件、H3全体200字条件、不要な保証否定を本文に添えない共通基準がある。条件未設定ではなく、今回の初稿が従わない事象である。通常content_auditは同じ読みやすさ検出器と独立した文章・整合性・情報整理の役割を持つが、今回の新しい本文に対する有料確認・Opus修正は未実行。したがって通常最終フローがこの本文を合格扱いした証拠はなく、修正できることも未証明。禁止事項を重ねて追加する根拠にしない。

構成とservice_mapは比較表に女性料金列を残す一方、MarriedGoの料金論点は採用しない設計。これが内部メモ型セルの入力側の具体的な緊張点である。掲載項目を確認済み情報と読者の判断に合わせる設計の問題として扱い、サービス固有の禁止句や「全社すべて同じ情報が揃わなければ比較不可」といった新条件へ拡張しない。保証境界が本文化されたことも入力と出力で観測したが、モデル内部の原因を断定しない。

証拠: /Users/ryoka/seo-saas/tmp/yabai-article-preflight/chapter-body-preflight/{preflight,test-plan,prepared-request-0..3}.json、chapter-body-test/{authorization,request-1..4,response-1..4,result,article.md,free-checks,manual-assessment,ledger,preview-status.png}。既存iabタブ10をhttp://127.0.0.1:8873/chapter-body.htmlへ切替え、実本文・141.30円・修正必要・有料最終校閲/本番未実施が表示されることを確認。次は既存の最終確認・Opus修正工程の範囲と費用を決めて検証する。今回の250円枠にその有料工程は含めていない。

## 2026-10-09: 比較表設計の共通基準整理と最終確認の無料見積もり

ユーザー「続けて」により、前回案内した表設計の整合性整理と有料確認前の費用計算を実施。reader_presentationの既存「個別商材の表」定義を置換し、読者の判断と採用回答の対応から比較列・個別表への配分を選ぶよう整理した。任意の項目が一部で未確認なら個別表での説明も選べるが、全社すべての情報一致を必須にせず、他社の確認済み強みや判断上必要な比較軸を一括削除しない。不存在と未確認の区別、内部採用記録を事実回答セルに使わない既存原則に沿った設計にする。step_outlineの既存の表設計指示をこの共通基準への参照へ置換。新しい禁止語・チェックキー・AI工程・サービス固有分岐は追加していない。定義見出しを変更したところ既存の定義数検査と構造契約からの参照が影響するため、見出しは既存名を維持した。修正後の無料テスト610件が通過。

既存構成のeditorial_policyはこの変更後の値と一致しない。元構成・研究確認・本文を新方針で合格済みに付け替えていない。今後の新規通常構成には再生成・検証が必要。今回の見積もりは保存済みの初稿を現在の品質基準で確認・修正する経路であり、新しい構成生成の再現性試験とは別である。本文の見出し・事実・元生成応答・元費用台帳は変更しない。

chapter-quality-preflightで既存focused_qualityの5役割の要求をモデル送信直前で捕捉。役割ごとの要求捕捉のためループ対象だけを1役割に限定し、応答や合格を合成せず各捕捉時に例外終了した。OpenAIの入力計数だけを呼び、出力応答APIは実行していない。初回の根拠範囲確認はLuna、他4役割はSol。正常max_output6000を維持した計数上限見積もりは1.7905/42.1050/25.4535/25.2160/25.8095円、計120.3745円。各応答1500出力トークンという仮定では83.9245円であり、実費予測の保証ではない。

Opus局所修正は、保存済みの手動指摘を対象とする15ブロックのサイズシナリオ。既存content_auditの修正要求を捕捉するためcost-onlyの不合格fixtureを使ったが、artifact保存を封じ、モデル校閲・合格・本番判定として使っていない。入力22584、通常repair_output_limitとthinking込み出力10372で予約上側64.6392円。実モデルが指摘する対象で費用・出力量は変わる。初回確認＋修正＋再確認の出力上限計算は305.3882円、各確認1500出力トークン仮定では約232.49円。根拠範囲の応答によって発生する限定原文照合・独立判断は、この数字には含めていない。根拠判断を確認せず源文チェックを省略したり、未実行を合格にしない。

次の候補は追加300円の硬い上限で、保存初稿の通常全文確認→必要なOpus局所修正1回→全文再確認1回。新しい検索・調査・構成生成・本文全再生成・手動個別編集・本番は含めない。現在は費用・範囲提示前の無料準備までで、有料実行未承認・未実施。根拠照合や追加指摘で上限に近づけば送信前に止まり、予算を自動拡張しない。従来の生成141.3008円とは別に記録する。

証拠: /Users/ryoka/seo-saas/tmp/yabai-article-preflight/chapter-quality-preflight/{preflight,tiered_article_*-request,tiered_article_*-count,repair-scenario-request}.json。原chapter-body-test/artifacts全件のsha256不変。計数API以外のネットワークは要求構築中封鎖し、有料要求の捕捉ではreserveを送信前で中断した。

## 2026-10-09: 保存本文の通常全文確認・Opus修正・全文再確認（未合格4段落）

ユーザー「すすめて」により、直前提案の追加300円を上限に、保存済みSonnet本文を通常focused_qualityの全5役割で確認、Opusの局所修正1回、全文再確認1回まで実施した。新しい調査・構成生成・全文再生成・個別の手編集・本番書込はなし。ARTICLE_LOCAL_PROSE_FINISH=0とラッパーで追加の修正送信を止める試験範囲。通常の本番上限・モデル・合格要件は変更しない。旧body/outline応答と台帳を維持した。

最初の隔離ケースchapter-full-quality-test-20261009は、Lunaの返却usageがSimpleNamespaceなのに試験記録側でmodel_dumpを呼び、1.2353円を消費後に保存失敗。これは製品フローの欠陥ではなく、この試験ラッパーの実装ミス。失敗台帳と応答IDを保存し、SDK型とSimpleNamespaceの両方を扱い、テキストを先に保存するよう試験記録を修正した。別の回復ケースchapter-full-quality-recovery-20261009は、先行1.2353円を差し引いた298.7647円を上限とし、試験全体の300円枠を維持した。回復ケースの最初の設定不一致はAPI送信前・0円で停止し、空の台帳を確認して同じケースで再開した。既存課金や回数を消去していない。

通常初回確認は、内部の省略指示、表/リスト参照不一致、不自然な日本語、593字の通常文章連続、3つの200字未満独立H3等を検出した。Opus1回の応答は24ブロックの編集を返したが、対象22ブロック外の2箇所（比較表の導入と累計条件の移し先の個別表）を含み、適用前に拒否された。追加のOpus送信は止めた。

原因は既存の修正指示と対象管理の不整合。確認指示はblock-0182の累計セルへ条件を統合するよう明示していたのに、その移し先は編集可能IDに入らなかった。また表を変更したときの案内文更新を既存修正システムが求める一方、案内文は参照専用だった。repair_packetで、現在の指摘のreasonが明示した実在IDと、対象の表の直前の通常段落だけを関連対象として伝えるよう整理した。未知IDは停止し、見出しや無関係な周辺段落を一括許可しない。validate_editsが自分で出した範囲外エラーをValueErrorとして捕捉し、汎用形式エラーへ上書きしていた点も修正した。step_content_auditの既存適用検査をapply_reviewed_editsへ移し、保存モデル応答の回復も新規修正と同じ検査を通す。合格要件・禁止語・AI工程・商材分岐は追加していない。

この修正後、保存済みの原Opus応答をそのまま再検証し、範囲・必要箇所・主張条件・300字連続・内部注釈・見出し対応の検査を通して隔離記事へ適用した。モデル応答を手編集していない。原応答sha256と適用前の全成果物をpre-revalidationへ保存した。再確認は同じ回復ケースの台帳・モデル呼出回数を保持して実施。

全文再確認で、Lunaが3つの原文照合へルーティングし、原文担当は対象のevidence_support1項目と3箇所の個別判定を返したが、受け取り側parse_focusが全4項目の返答を要求して停止した。これは候補への範囲絞りとパーサーの不整合。tiered_evidenceで、その要求のcandidate_checksに含まれるcheckキーだけを厳密検査するよう修正し、対象外の既存全体判定は呼び出し元で維持する。対象check/個別IDの欠落・重複・failと集約passの不一致、未提示原文を確認済み扱いすることを拒否する検査は維持。追加照合で未解決が減る場合も、今回のcandidate_checksのキーを使う。回帰試験は、以前対象外のpass保持を検証していたfixtureを、実際にルートされた比較checkの保持と未解決だけへの縮小を検証するものへ変更した。保存済み範囲確認・原文照合を通常キャッシュから再利用し、再送せず残り4役割を完了した。

無料回帰614件通過。最終結果は未合格。現本文10,413字、300字超の通常文章連続・200字未満独立H3・編集用の省略指示・元の表/リスト参照不一致は解消。根拠・比較条件・数値条件・網羅性・未完成表示・結論整合性は通過。残った指摘は4段落: block-0064（見出し後半の回答を第一文で先送り）、block-0086（「金銭を必要とするメール」の修飾）、block-0102（「経路を防ぐ」の語の組み合わせ）、block-0172（比較H2で個別機能を再列挙）。失敗キーはprose_quality/redundancy。新たな事実不足・調査のやり直しではない。初回全文確認が見落とした表・リストの表現2件を再確認が発見したことも記録し、通常1修正で必ず収束すると結論づけない。

実費195.3534円（内部料金・USDJPY200換算）。記録失敗1.2353円を含む計13回: Luna4、Sol8、Opus1。全部費用確定。300円枠の未使用104.6466円。本文生成の前回141.3008円とは別。追加修正・3回目全文確認・最終構造合格・本番反映は未実施。通常の局所文章完了は表/リスト変更やredundancyのfailを対象にできず、今回の指摘へ合格用に拡張していない。metadata.content_auditedはfalseのまま。

次の実行前の無料計数も実施。残4箇所＋表の導入という関係対象5ブロックの通常Opus修正の予約上限45.3888円、現本文に対する5役割の出力最大上限120.7023円、合計166.0911円（修正後の未知本文や条件付き追加原文照合を含まない）。未使用枠105円と追加確認不要50円を足しても最大要求を賄えないため、承認なしに次の有料修正へ入らない。追加100円なら合計400円枠、残約205円でこの1修正＋1全文確認を限定できる見積もりだが、未実行かつ合格保証ではない。

証拠: /Users/ryoka/seo-saas/tmp/yabai-article-preflight/chapter-quality-test/{result,ledger,exception}.json、chapter-quality-test-recovery/{authorization,result,ledger,request-1..12,response-1..12,patch-revalidation,remaining-findings,free-checks,pre-revalidation,pre-source-parser-fix,article.md,preview-status.png}（失敗初回はresponse本文未保存、usage/応答IDは台帳に保存）、chapter-quality-next-preflight/{preflight,repair-request}.json。実記事プレビューhttp://127.0.0.1:8873/chapter-quality.html、元の初稿chapter-body-original.htmlを保持。最新構成からのゼロ開始・本番完了状態の証明ではない。

## 2026-10-09: 追加100円承認後の1局所修正・全文再確認と、残件の由来

直前の追加100円提案に対する「続けて」を承認として、品質確認の通算硬上限を300円から400円へ拡張。旧authorization・全成果物・台帳をpre-authorized-extension-400へ保存し、authorization-extension-400.jsonと台帳のauthorization_extensionsに直接の承認を記録。費用・既存12call・修正回数を消去せず、同一job/caseで追加Opus修正1回と全5役割の確認1回だけを実施した。生成や追加調査、本番変更、手編集、prose finishの追加はなし。

Opus局所修正は21.4128円で、前回の4指摘と表導入という関係箇所の計5ブロックだけを変更。前回4指摘は今回の全文確認では解消した。見出しは8H2/17H3を維持し、H4追加なし。本文10,347字、delivery/structure/readabilityの全文機械検査はすべて空。300字超の通常文章連続と200字未満独立H3はない。これだけで意味的合格とは扱っていない。

途中、試験ラッパーが既存の条件付きevidence_adjudicateを許容リストに入れていなかったため、送信前に止まった。これは私の試験制限の漏れ。既存の限定原文照合・独立確認の範囲として許可し、台帳/回数を維持したまま再開。scope/screenの取得応答は正常キャッシュで再利用し、追加の再送費は0円。

さらに製品側の原文照合引継ぎ不整合を特定。一次照合は8件中6件をpass、2件をfailと判定。独立確認にcandidate_checksとして残2件のaffected_blocksだけでなく、前段の全8件のclaim_verdictsと同じ8段落を渡していた。モデルは提示された8件の個別結果を返したが、validatorがaffected_blocksの2件との完全一致だけを要求して拒否した。指摘欠落ではなく、現在の判定対象と引継ぎ済みpassの扱いの不一致だった。

tiered_evidence.validate_claim_verdictsを、未解決IDの判定は引き続き全件必須とし、明示的に引き継がれた旧passの返答は任意に受け付ける形へ整理した。未知ID・重複・未解決IDの欠落・旧passの新たなfailへの転用・個別failと集約passの不一致は拒否する。引継ぎpassはモデルが復唱しても前段の検証済みの理由へ戻し、復唱がなくても保持する。指摘内容を合格へ変える処理ではない。新しいプロンプト禁止事項・商材分岐・モデル工程は追加していない。回帰616件通過。保存された独立確認応答も通常キャッシュのまま再利用し、追加再送なしで残り4役割を実施できた。

結果は未合格。coverage/unfinished_content/comparison_conditions/metric_scope/unsupported_guarantees/redundancyはpass。evidence_support/conclusion_consistency/prose_qualityはfail、対象は10ブロック。全10ブロックの本文は追加修正前の同IDと完全一致しており、この修正で新たに作られた不具合ではない。2回の以前の確認では拾われず、今回の再確認で新たに指摘されたもの。確認の安定性と過剰検出の切り分けを含む未解決課題として記録する。

明確に既存要件と照合して直せるのは、不要な保証否定3段落（block-0096/0146/0168）と「料金は各社の紹介で扱います」という案内の掲載範囲不一致（0174、実際の料金掲載は3社）。残りは指摘の妥当性確認が必要: 親H2の全文には「調査と体験談」もあるのに「どのくらい？」部分のみで体験談を範囲外と扱う指摘（0072/0080）、警戒理由と本人確認の一般的な仕組み説明を原文に同一の説明がないとしてfailにした2件（0122/0160）、意味が伝わる言い回しの改善とまとめリストの導入追加（0202/0224）。この記録はモデル判定を手動で合格へ上書きするものではなく、次の修正対象の妥当性整理用。盲目的に10箇所全部を構成移動/追加ルールへ変換しない。

今回は8callで120.7547円: Opus21.4128、Luna scope1.2631/screen1.3603、Sol原文独立確認27.3350、coverage29.7965/language12.1075/consistency12.5465/organization14.9330。品質確認累計316.1081円（先行の試験記録失敗1.2353を含む）、400円枠残83.8919円。内部USDJPY200換算。本文生成141.3008円、構成236.4349円とは別。追加修正要求は試験回数ガードで送信前に止め、3回目Opusは0。現metadata.content_audited=false、final_validate未実行、本番未反映。通常本番の予算上限は変えていない。

プレビューはローカルサーバ停止から復旧し、同じchapter-quality.htmlをブラウザの新しいタブで表示確認。「前回4箇所は解消／全文確認は未合格」と316.11/400円の表示、本文を確認。旧画像を上書きせずpreview-extension-status.pngへ保存。今回の確認が旧保存構成を使った本文試験であり、最新構成からのゼロ開始や本番完了状態の証明ではないことを表示する。

証拠: /Users/ryoka/seo-saas/tmp/yabai-article-preflight/chapter-quality-test-recovery/{authorization-extension-400,result,ledger,request-13..20,response-13..20,pre-authorized-extension-400,free-checks,remaining-findings,finding-provenance,article.md,preview-extension-status.png}、/tmp/dig-chapter-followup-tests.log。次は追加の有料全文試験を先行させず、明確な生成漏れと、過剰検出・初回確認漏れ・旧構成と現行基準の差を切り分けて修正対象を確定する。全文再生成・予算自動拡張で収束させるとはしていない。

## 2026-10-13: 確認指示の衝突整理、空行なし見出しの引継ぎ、50円以内の判定校正

ユーザー「つづけて」に従い、前回の新指摘10ブロックを実不具合と過剰検出に切り分けた。backendはcodex/outline-intent-design、HEAD fa9d644で、未追跡の別用途ファイルは維持。保存本文SHA256 4448a00487fdef17fa94d4fa425ff38899959b59254a356e40d07300039158c2と前回の全成果物・台帳を保持。今回も本文の個別編集、全再生成、本番更新はしていない。

指示の不整合を特定・修正した。EDITORIAL_SYSTEMの「全項目pass」例に、現在の共通提示基準が不要としている一般的な保証否定が残っていた。例を対象・有料条件・実際の機能範囲だけで成立する文へ置換。step_reviewの表/リスト導入追加指示が2箇所あり、必ず定型文を足すことになっていたため、共通提示基準に集約して、見出し・既存本文から役割が分かるかと実際の掲載範囲との対応で判定する形へ置換。章の範囲も見出し全文と役割から判定するよう既存の定義を整理し、主要な問いへの回答や補足の必要性の検査は維持した。

機能の作用から説明できる価値と、未確認の仕様・実測効果・比較順位・専門的判断・保証を区別する既存要件を、原文担当にも届く共通REVIEW_RESOLUTION_POLICYへ整理。原文に同一表現がないことだけで不合格にしない一方、根拠を超える前提・推論は特定する。source側POLICYにあった「原文への一致」一辺倒の説明もこの共通基準へ揃えた。EVIDENCE_POLICYそのものはHEADとバイト単位で同一に保ち、調査計画・調査台帳の採用方針を変更して再調査へ戻すことは避けた。確認の基準/文脈が変わった旧合格を新判定として転用しない。

実装上の見出し引継ぎ不具合も確認。content_blocksは空行区切りなので、直前本文とH2が同じblock-0072に入っていた。scoped_packetはブロック先頭だけを見出しとして認識し、体験表block-0086へ旧「違法？不倫になる境界線」のH2を渡していた。段落ID・本文を変えず、ブロック内の見出し行を読み取るよう修正。コードフェンス内の見出し例は章として扱わず、空行なしの本来の親見出しとフェンスを跨ぐ文脈の回帰2件を追加。回帰618件通過。原文・局所修正用の文脈引継ぎの欠陥が証明されたものであり、この不具合だけが全文organizationの過剰検出原因だったとは断定しない。

既存の「今後50円以内の追加は確認不要」許可を使い、独立した判定校正ケースreview-alignment-probe-20261013を最大50円で実施。旧316.1081円と合算し、従来の400円枠を超えないことも記録。旧ケースの20callや回数を消して同じ全文修正を再試行したものではなく、異なる確認基準の校正試験。記事修正や全文合格試験ではない。通常のSol organization/languageの全文入力要求と、旧不合格の原文2箇所に対する通常Luna限定照合を使用し、各出力6000は維持。通常の出力生成前に入力計数し、送信前の予約上限で管理した。

初回3callは26.7372円。languageはpass、organizationもpassとなり、旧章範囲2件・まとめ導入1件・言い回し1件は出なくなった。ただし、残っている不要な免責3段落までpassになったため、この結果を成功・全記事合格とは扱わない。任意の改善と必須品質要件の違反の区別が不十分だった。共通の確認結果の扱いを、明示指定・共通提示基準への適合を確認したうえで任意の改善を非差戻しとする定義へ修正。EFFECT_CLAIM_POLICYも「削除してよい」という許容だけではなく、既存本文の不要な免責は提示基準の違反として修正することへ整理した。

同じ50円台帳を維持し、基準修正後の追加校正を実施。3免責の実在IDと実際の周辺段落・親見出しへ絞った既存organization担当の陰性対照をSol1回、原文2箇所をLuna1回で確認。追加9.9674円、同一試験累計36.7046円。Solはblock-0096/0146/0168の不要な保証否定をすべてfailとして具体的に指摘し、0168の自衛策の提案は保持するよう判定した。これは指定3箇所の検出確認であり、最終修正版の全記事organizationが再び漏れなく検出することまで証明していない。

原文2件はどちらの校正でもfailのまま。block-0122は警戒サインから共犯者の待ち伏せ・脅迫材料収集という具体的事情へ説明を広げている点、0160は本人確認から利用者の状況把握・遭遇リスク低減という効果へ広げている点。Lunaは「可能性」と書かれた本文を「断定」と説明している箇所もあり、合理的な推論を認める共通基準に完全に従えたとは断言しない。手動でsource passへ上書きせず、取得応答・訂正用引用・個別判定を保持。確認済み商材機能のメリット説明は合意済みとして維持する。一方、資料に明記されていない具体的な危険場面の推測まで警戒理由として掲載するかは追加の判断事項として、ユーザーへ例と2択を提示した。この回答待ちで該当方針を推測して変更しない。

明確な修正対象は不要免責3段落と、料金を載せない会社もあるのに「各社の紹介で扱います」とする案内block-0174。削除のみの機械的影響を保存本文から別の私的シナリオで確認し、delivery/readabilityへの違反が増えないことを確認した。シナリオは記事として保存・公開しておらず、意味的合格例でもない。実際の通常モデル修正はまだ送信していない。料金案内の旧consistency failも今回の校正passで消去していない。

実費5call、36.7046円/50円: Sol organization13.5015、Luna source1.2887、Sol language11.9470、Sol organization8.6330、Luna source1.3343。品質確認の合算352.8127円/承認400円、残47.1873円。旧成果物全JSONのSHA256不変を各実行終了時に確認。研究採用、記事完成状態、広告CTA、フロント表示、本番の接続移行は未変更。ARTICLE_LOCAL_PROSE_FINISH=0、生成/修正モデルを今回変更していない。

証拠: /Users/ryoka/seo-saas/tmp/yabai-article-preflight/review-alignment-probe-20261013/{authorization,preflight,second-preflight,result,ledger,request-1..5,response-1..5,organization-review,language-review,source-review,second-calibration/*,deletion-impact}.json。前半のaudit_articleを評価用に単一roleへ絞った戻り値には未実行keyの合成pass欄があるため、評価結果はphases[role].checksまたはsourceの個別checksだけを読む。全記事合格へ変換しない。原応答と検証範囲を保持する。UIの実操作は今回不要であり、新しいユーザーAGENTSに従ってブラウザ操作や起動権限の変更はしていない。

## 2026-10-13: 一括修正の要求構築・計数（生成せず、方針と追加費用の回答待ち）

ユーザーの追加「続けて」で独立作業を進めた。危険場面の推測をどこまで載せるかの2択にはまだ選択がないため、該当2箇所の掲載方針は推測で確定しない。確実な免責3段落と料金案内の計4箇所、および原文担当がfailとして残した2箇所を含む計6箇所の両案を無料で準備した。

step_content_auditの既存の修正要求構築をprepare_content_repairへ抽出。通常runも同じ関数を呼び、これまでと同じ指示・モデル・上限・修正パケット・再提出のメッセージ履歴・適用検査を使う。工程やモデル呼び出しは追加していない。これにより前回の全文判定へ新しいsnapshotや合格項目を付け足すことなく、実在する失敗指摘だけから通常の修正要求を計数できる。preflightのreportはrepair_findings_onlyであり、全記事監査結果・チェックポイントではない。未実行checkの合成passを作らず、保存済み記事のcontent_auditedや本番の完了状態は変更しない。

抽出時のインデント不具合は無料試験で検出し、送信前に修正。元の複数行プロンプト文字列の内容を保持して抽出し直した。要求準備で送信/監査保存が行われないこと、未知の対象IDを副作用前に拒否することの2回帰を追加。全620件通過。

Anthropic/OpenAIの入力トークン計数だけを使用。有料の出力生成0、修正API送信0、記事変更0。4箇所の実指摘からの通常Opus要求はinput21262/output6000（thinking込み）、送信前の予約上限45.9880円。6箇所案は26469/6000、50.5696円。パケットには既存指摘が参照した文・表・対象表の案内の関連IDも含むため、編集可能IDは前者7、後者10だが、変更必須の指摘は4/6箇所であり全文修正ではない。

現在の未変更本文に対する正常な5役割要求の計数上限はLuna scope1.8101、Sol coverage42.4970/language25.7270/consistency25.5910/organization26.1695円。合計121.7946円。修正1回+全文確認1回の予約上側は4箇所167.7826円、6箇所172.3642円。これは修正後の未知本文への保証ではなく、条件付き原文照合/独立再確認を含まない。前回の同種実費120.7547円を参考として示し、上限と実費を混同していない。

品質確認の通算352.8127円/承認400円、残47.1873円は不変。現残額で有料一括修正+全文確認を始めない。推論表現の回答後、追加200円まで（通算600円）で修正1回→全文確認1回を実施してよいかをユーザーへ提示。追加修正を自動反復しない。今回の準備でその承認を受けた扱いにはしていない。

次の候補は、既存の要求・適用検査・全文検査を使った保存済み本文の隔離試験であり、最新構成からのゼロ開始/本番完了を証明するものではない。古い全文監査の合格を新基準へ転用せず、記録された指摘からの修正候補は未確認のまま保存し、実際の全文確認と最終検査で判定する。元の全成果物・台帳のSHA256不変を再確認した。

証拠: /Users/ryoka/seo-saas/tmp/yabai-article-preflight/batched-repair-preflight-20261013/{preflight,clear_four-request,clear_four-findings,six_if_source_restricted-request,six_if_source_restricted-findings}.json、/tmp/dig-repair-preflight-refactor-tests.log。今回の生成課金は0円。ブラウザ実操作・Claude委譲は不要で行っていない。

### 2026-10-13: user narrowed execution to repairing existing problem passages

User: 「じゃあ問題箇所の修正だけでいいので、それで進めて」. Executed one normal prepared clear-four Opus repair, with the existing remaining quality allowance of 47.1873 JPY; no new budget increase, full audit, research, or production write. One call cost 19.5736 JPY; cumulative quality expense 372.3863 / 400 JPY. The exact pre-counted request was used unchanged.

Only block-0096, block-0146, block-0168, block-0174 changed. Deleted blanket disclaimers, retained concrete self-protection advice, and aligned the pricing introduction to the three services whose sections contain prices. Normal repair application gates passed; headings and the two policy-pending source passages remained unchanged. This is a saved repair candidate, not a full-quality pass or production replacement.

Evidence: /Users/ryoka/seo-saas/tmp/yabai-article-preflight/clear-four-repair-20261013/{result.json,request.json,response.json,changes.json,free-verification.json,article.md}. Candidate SHA256 ebf8c9df2ec13486a198bae0735a61bb6737560ac47f60af75690946bbe5b85d. Original historical case artifacts remain unchanged. No paid retries.

### 2026-10-13: preparation for normal writing/review recurrence check

User requested next step after common repair vs individual article repair was clarified. Found and removed two remaining unconditional table/list-introduction requirements in step_article.SYSTEM_PROMPT; writer now defers to existing shared contextual introduction rule. No new prohibition or model stage. Added composed-prompt regression; 621 tests passed (/tmp/dig-recurrence-preparation-tests-20261013.log).

Actual isolated normal service_map entry rejected the historical outline due to stale editorial_policy. No accepted status/hash was synthesized. A separate count-only scenario mocked guards ONLY during request capture, never during generation or artifact acceptance; no downstream pass was saved. Existing data remained immutable. Initial current outline audit upper 38.7925 JPY; mapping/body conditional ceiling projection 215.8806 JPY; previous current-body full-review ceiling reference 121.7946 JPY; combined 376.4677 JPY excluding conditional source calls and unknown new body size. Generation fee this preparation: 0. Proposal pending: +400 JPY, aggregate cap800; current spent372.3863. No automated repair/retry authorized; if outline fails, stop before generation.

Plan and counts: /Users/ryoka/seo-saas/tmp/yabai-article-preflight/{recurrence-preflight-20261013/plan.json,recurrence-cost-scenario-20261013/preflight.json,recurrence-outline-preflight-20261013/preflight.json}. This proves guard behavior and prompt consistency, not generated output quality, current source freshness, or production behavior.

### 2026-10-13: authorized recurrence run stopped at API credit rejection

User 「続けて。」 authorized the just-proposed +400 JPY recurrence test, aggregate cap800. Isolated case recurrence-normal-writing-20261013 uses previous confirmed total372.3863, remaining427.6137, generation allocation225 and quality202.6137. First current outline audit request was sent once. Provider stream returned credit_balance_exhausted / insufficient_quota; no result, no body generation, no production changes, no paid retry.

One ledger entry remains unknown_cost_reserved, 38.7925 JPY reservation. Confirmed prior cost stays372.3863; 411.1788 is confirmed plus reservation, NOT actual billed expense. This unknown amount is retained on any explicit retry, not erased or reset via a new case. Prepared syntax-checked --resume-user-message path requires direct user retry authorization after adding credits, verifies the sole failed request's credit error, and resumes same outline request/case/cap. It was not executed.

Evidence: /Users/ryoka/seo-saas/tmp/yabai-article-preflight/recurrence-run-20261013/{authorization.json,request-1.json,result.json,exception.txt,ledger/}; /Users/ryoka/seo-saas/tmp/yabai-article-preflight/execute_recurrence_20261013.py. API troubleshooting skill used to classify actual provider error. No browser delegation required.

### 2026-10-13: credit recovery completed provider call, but harness lost result

User 「API追加した。」 authorized resuming identical request/case/cap. Corrected credit-error diagnostic lookup in the evaluation script (actual ledger field diagnostics, not interruption), before any provider request. Original request equality check passed. OpenAI request completed after73.1 seconds and accounted30.827 JPY. The harness then incorrectly called model_dump on OpenAI SimpleNamespace usage while preparing the response record; AttributeError occurred before the paid response text was written. No model result/cache can be reused; did not resend or override quality.

This is an agent-authored EVALUATION RECORDER failure, not evidence of the article's quality or a production-path defect. Fixed local recording helper to durably save text before metadata handling and support SDK model_dump as well as SimpleNamespace. Three free tests passed: both provider usage types, plus raw paid text retention on metadata serialization error. New helper used by the recurrence script; no production source changes in this turn. No automatic extra paid audit.

Confirmed cumulative expense403.2133 JPY (=372.3863+30.827); prior credit-rejection unconfirmed reservation38.7925 retained; budget consumption incl reservation442.0058 /800, remaining357.9942. Outline judgment unavailable, body not started, production unchanged. Further audit would be an additional paid request beyond the agreed one-check sequence and requires explicit retry scope authorization.

Evidence: /Users/ryoka/seo-saas/tmp/yabai-article-preflight/recurrence-run-20261013/{result.json,request-2.json,exception.txt,ledger/}; model_result_recording.py and test_model_result_recording.py in same preflight root. Do not describe this as a successful outline audit or charge38.7925 as confirmed actual.

### 2026-10-13: approved lost-response repeat saved; one genuine outline condition failure

User 「大丈夫です。」 explicitly approved one extra identical outline check, initial reservation38.7925 JPY (<39). Kept all old costs/counts/reservation and aggregate cap800. Evaluation-only duplicate-suppression release applied to the precisely identified accounted lost-response receipt in a deep-copied guard view; original ledger unchanged, all amount/role/count/category/total gates retained. Outline phase reallocated locally to140 JPY while shared quality allocation202.6137 and overall cap800 remained unchanged; no production guard/default changes. Paid response text saved before metadata via tested recorder.

Audit completed in86.1 seconds; actual32.031 JPY. Only evidence_support failed at outline block-0090: fixed sequence says recording before dangerous-scene retreat and loses source's conditional “可能なら”. q12 fetched passage explicitly says prioritize physical safety and immediate retreat if aggressive/violent. Verified against source adoption record; meaningful condition loss, not identical-wording over-detection. Coverage, comparison conditions, consistency, metric scope, unfinished content, guarantees passed; article-language checks not applicable at outline stage. No source follow-up/new research. Stopped before body generation per scope.

Prepared a free separate candidate patch to block-0090 only: danger/safety priority before numbered actions, recording conditional on safely possible. Original outline untouched. Structural and topic-assignment checks passed, normal candidate review request captured/counted at upper38.8135 JPY plus conditional sources. No extra paid repair/check. This is not outline acceptance or normal-generation success.

Confirmed cumulative435.2443 JPY, unconfirmed original quota-rejection reservation38.7925, budget usage474.0368 /800, remainder325.9632. Evidence: /Users/ryoka/seo-saas/tmp/yabai-article-preflight/recurrence-run-20261013/{response-3.txt,response-3.json,outline-review.json,result.json,ledger/}; /Users/ryoka/seo-saas/tmp/yabai-article-preflight/recurrence-condition-candidate-20261013/{changes.json,review-request.json,preflight.json}. Candidate source fields and other requirements remain unchanged.

### 2026-10-13: normal body generated and full audit completed, not accepted

User 「続けて。」 approved changed-condition outline recheck then normal generation/full review on pass, cap800 retained. q12 condition candidate was applied locally as an unaudited candidate. Changed request matched counted request exactly. Recheck actual30.684 JPY passed; genuine current audit snapshot recorded before outline policy migration. All prior ledger costs/counts retained; approved one extra outline audit role and its39 JPY reservation. Category allocation changed locally from225/202.6137 to185/242.6137 generation/quality to account for already-spent outline checks while preserving aggregate427.6137 remaining-case cap.

Normal service_map24.8964, body116.4728 (three Sonnet5.5 calls), two CTAs injected by normal function, full tiered review75.1255. This approved run actual247.1787; no paid repair. Saved article10047 characters incl CTAs, 8H2/17H3. Full current article audit valid=false. Failures include general guarantee denials, internal missing-information cell, reader-confirm fallback, one487-char prose run, two thin H3s, three language items, heading-first-answer/comparison-detail placement. No metadata/pass overrides. Source support of “職場での評価” is a plausible over-detection boundary and must not be presented as unequivocally invented fact. Article final validation was not run because full audit failed. The evaluator blocked the normal Opus repair before sending, as only generation and one full review were authorized.

Prompt provenance shows stale outline internal clause「各項目は安全を保証するものではなく、確認の目安」reappeared nearly literally in body. Shared reader policy did not reliably prevent it. Additionally, WRITING_POLICY sent the full research adoption contract to authoring. Free code change introduced PUBLICATION_WRITING_POLICY using existing fact boundaries+reader presentation; article authoring and content repair now use it, while research/planning/independent QA retain the unchanged full evidence contract. No new bans, stages, provider routes or research-policy changes.629 tests passed (/tmp/dig-authoring-boundary-tests-20261013.log). This reduces mixed responsibilities but has NOT yet proven new generated quality or elimination of stale brief constraint echo.

Current confirmed cumulative682.423 JPY; quota-rejection unknown reservation38.7925 retained, budget usage721.2155 /800, remaining78.7845. Normal repair plus one whole-review count-only scenario191.5276 (repair70.4856, full review reference121.042), excluding conditional source calls/unknown repaired text. No extra paid calls in preparation. Evidence under /Users/ryoka/seo-saas/tmp/yabai-article-preflight/recurrence-run-20261013 and recurrence-completion-preflight-20261013. Article availability is not acceptance or frontend/production reflection.
