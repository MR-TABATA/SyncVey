# SyncVey — Unit Test 仕様書

実行日: 2026-06-02  
結果: **70 passed / 0 failed**（E2E除く Unit。E2E 19 を含む全体は 89 passed）  
実行コマンド: `DATABASE_URL=sqlite:///test.db python -m pytest asset_manager/tests/ -v --ignore=asset_manager/tests/test_e2e.py`

---

## test_core.py — コアロジック

### TestComputeRawDiff（差分計算）
| テスト | 検証内容 |
|---|---|
| test_detects_added_field | tfstateにないフィールドがAWS側に追加されたことを検知する |
| test_detects_changed_value | フィールド値が変更されたことを検知する |
| test_detects_multiple_changes | 複数フィールドの同時変更を検知する |
| test_detects_removed_field | tfstateにあったフィールドがAWS側から消えたことを検知する |
| test_empty_both_returns_empty | 両方空の場合は差分なしを返す |
| test_excludes_noise_fields | 動的パラメータ（ECSタスク数等）はDriftとして扱わない |
| test_identical_dicts_return_no_diff | 同一データは差分なしを返す |
| test_result_is_sorted_by_field_name | 差分結果はフィールド名でソートされる |

### TestResolveResourceType（リソース種別判定）
| テスト | 検証内容 |
|---|---|
| test_ec2_instance | aws_instance → EC2 に解決される |
| test_ecs_fargate_case_insensitive | FARGATEの大文字小文字を区別しない |
| test_ecs_fargate_detection | ECS Fargateを正しく判定する |
| test_ecs_service_defaults_to_ecs | ECSサービスのデフォルト判定 |
| test_lambda_function | aws_lambda_function → Lambda に解決される |
| test_rds_instance | aws_db_instance → RDS に解決される |
| test_resolve_provider_aws | AWSプロバイダーを正しく解決する |
| test_resolve_provider_unknown_prefix | 不明なプレフィックスのフォールバック |
| test_s3_bucket | aws_s3_bucket → S3 に解決される |
| test_unknown_resource_returns_something | 未知のリソースタイプでもクラッシュしない |

### TestRunScan（スキャン統合）
| テスト | 検証内容 |
|---|---|
| test_creates_assets_from_ec2 | EC2スキャンでAssetが作成される |
| test_creates_assets_from_rds | RDSスキャンでAssetが作成される |
| test_empty_aws_account_has_no_ec2_or_rds | 手動作成リソースがなければEC2・RDSは0件 |
| test_result_counts_are_consistent | scanned = created + updated が常に成立する |
| test_second_scan_updates_raw_data_prev | 2回目スキャンでraw_data_prevに前回値が保存される |

---

## test_scanner.py — Boto3スキャナー

### TestScanEc2
| テスト | 検証内容 |
|---|---|
| test_empty_account_returns_empty_list | EC2なしの場合は空リストを返す |
| test_finds_multiple_instances | 複数インスタンスを全て検出する |
| test_finds_running_instance | 起動中インスタンスを検出し属性を正規化する |
| test_instance_type_change_is_detectable | インスタンスタイプの変更を検知できる |
| test_normalizes_tags_to_dict | Tags配列を{key: value}の辞書に変換する |

### TestScanRds
| テスト | 検証内容 |
|---|---|
| test_empty_account_returns_empty_list | RDSなしの場合は空リストを返す |
| test_engine_version_change_is_detectable | エンジンバージョンアップを検知できる |
| test_finds_db_instance | DBインスタンスを検出し属性を正規化する |
| test_multi_az_field_present | multi_azフィールドが含まれる |

### TestScanEcsServices
| テスト | 検証内容 |
|---|---|
| test_desired_count_change_is_detectable | desired_countの変更を検知できる |
| test_empty_account_returns_empty_list | ECSなしの場合は空リストを返す |
| test_finds_service | ECSサービスを検出し属性を正規化する |

### TestScanS3
| テスト | 検証内容 |
|---|---|
| test_empty_account_returns_empty_list | S3なしの場合は空リストを返す |
| test_finds_bucket | バケットを検出し属性を正規化する |
| test_finds_multiple_buckets | 複数バケットを全て検出する |

### TestScanAlb
| テスト | 検証内容 |
|---|---|
| test_arn_and_dns_name_present | ARNとDNS名が属性に含まれる |
| test_empty_account_returns_empty_list | ALBなしの場合は空リストを返す |
| test_finds_load_balancer | ロードバランサーを検出し属性を正規化する |

### TestScanVpc
| テスト | 検証内容 |
|---|---|
| test_cidr_change_is_detectable | CIDRブロックの変更を検知できる |
| test_finds_created_vpc | VPCを検出しCIDRを正規化する |
| test_resource_type_is_correct | _resource_typeがaws_vpcである |

### TestScanEbs
| テスト | 検証内容 |
|---|---|
| test_empty_account_returns_empty_list | EBSなしの場合は空リストを返す |
| test_finds_volume | ボリュームを検出しサイズ・タイプを正規化する |
| test_volume_type_change_is_detectable | gp2→gp3等のボリュームタイプ変更を検知できる |

### TestScanLambda
| テスト | 検証内容 |
|---|---|
| test_empty_account_returns_empty_list | Lambdaなしの場合は空リストを返す |
| test_finds_function | Lambda関数を検出し属性を正規化する |
| test_runtime_change_is_detectable | ランタイムバージョンアップを検知できる |

---

## test_notifications.py — Slack通知

### TestSendDriftNotification
| テスト | 検証内容 |
|---|---|
| test_no_drift_returns_false | Driftがない場合は通知しない |
| test_no_webhook_returns_false | Webhook未設定の場合は通知しない |
| test_sends_when_drift_detected | Drift検知時にSlackに送信する |
| test_sends_when_new_resources_found | 管理外リソース発見時にSlackに送信する |
| test_slack_error_returns_false | Slackエラー時はFalseを返す |
| test_rejects_ssrf_and_non_slack_webhooks | SSRF/file:/非Slackホスト/非httpsのWebhookは送信せず urlopen も呼ばない（[security-check.md](security-check.md) 参照） |

### TestBuildSlackMessage
| テスト | 検証内容 |
|---|---|
| test_message_contains_env_info | メッセージに環境情報が含まれる |
| test_message_contains_system_name | メッセージにシステム名が含まれる |
| test_message_mentions_added_count | 追加リソース数がメッセージに含まれる |
| test_message_mentions_changed_count | 変更リソース数がメッセージに含まれる |

---

## test_secrets.py — 機密情報検出

### TestDetectSecrets
| テスト | 検証内容 |
|---|---|
| test_counts_across_multiple_resources | 複数リソースの機密情報を集計する |
| test_detects_multiple_secret_fields | 複数の機密フィールドを検出する |
| test_detects_password_field | passwordフィールドを機密として検出する |
| test_empty_tfstate_returns_empty | 空のtfstateは空リストを返す |
| test_ignores_already_scrubbed_values | スクラブ済み値は再検出しない |
| test_ignores_data_sources | data_sourceブロックは検査対象外 |
| test_no_secrets_returns_empty | 機密情報がない場合は空リストを返す |

### TestScrubSecrets
| テスト | 検証内容 |
|---|---|
| test_non_secret_fields_unchanged | 機密でないフィールドは変更しない |
| test_scrubs_multiple_patterns | 複数の機密パターンをマスクする |
| test_scrubs_password | passwordフィールドをマスクする |
