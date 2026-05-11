from __future__ import annotations

import json
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from statsmodels.tsa.arima.model import ARIMA


MAX_DATA_FILE_BYTES = 100_000_000
MAX_DATA_ROWS = 200_000
MAX_UNIQUE_EXAMPLES = 8


@dataclass(frozen=True)
class DataTools:
    workspace: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", self.workspace.resolve())

    def resolve_path(self, requested_path: str) -> Path:
        candidate = (self.workspace / requested_path).resolve()
        if candidate != self.workspace and self.workspace not in candidate.parents:
            raise ValueError(f"Path is outside workspace: {requested_path}")
        return candidate

    def load_table(self, path: str, max_rows: int = MAX_DATA_ROWS) -> pd.DataFrame:
        file_path = self.resolve_path(path)
        if not file_path.is_file():
            raise ValueError(f"Not a file: {path}")
        if file_path.stat().st_size > MAX_DATA_FILE_BYTES:
            raise ValueError(f"Data file is too large: {path}")

        suffix = file_path.suffix.lower()
        if suffix == ".csv":
            frame = pd.read_csv(file_path, nrows=max_rows)
        elif suffix in {".xlsx", ".xls"}:
            frame = pd.read_excel(file_path, nrows=max_rows)
        elif suffix == ".parquet":
            frame = pd.read_parquet(file_path)
            if len(frame) > max_rows:
                frame = frame.head(max_rows)
        else:
            raise ValueError("Supported table formats: .csv, .xlsx, .xls, .parquet")

        return frame

    def profile_table(self, path: str, max_rows: int = MAX_DATA_ROWS) -> str:
        frame = self.load_table(path, max_rows=max_rows)
        numeric = frame.select_dtypes(include="number")
        datetime_columns = _datetime_ranges(frame)

        columns: list[dict[str, Any]] = []
        for column in frame.columns:
            series = frame[column]
            examples = series.dropna().astype(str).head(MAX_UNIQUE_EXAMPLES).tolist()
            columns.append(
                {
                    "name": str(column),
                    "dtype": str(series.dtype),
                    "missing": int(series.isna().sum()),
                    "missing_pct": round(float(series.isna().mean()), 4),
                    "unique": int(series.nunique(dropna=True)),
                    "examples": examples,
                }
            )

        payload: dict[str, Any] = {
            "path": path,
            "rows_loaded": int(len(frame)),
            "columns": int(len(frame.columns)),
            "numeric_columns": numeric.columns.astype(str).tolist(),
            "datetime_ranges": datetime_columns,
            "column_profile": columns,
        }

        if not numeric.empty:
            payload["numeric_describe"] = _records(numeric.describe().transpose().reset_index())

        return _to_json(payload)

    def missing_report(self, path: str, max_rows: int = MAX_DATA_ROWS, top_n: int = 30) -> str:
        frame = self.load_table(path, max_rows=max_rows)
        report = (
            frame.isna()
            .sum()
            .rename("missing")
            .reset_index()
            .rename(columns={"index": "column"})
        )
        report["missing_pct"] = (report["missing"] / max(len(frame), 1)).round(4)
        report = report.sort_values(["missing", "column"], ascending=[False, True]).head(top_n)
        return _to_json({"rows_loaded": int(len(frame)), "missing_report": _records(report)})

    def correlations(
        self,
        path: str,
        method: str = "pearson",
        target: str | None = None,
        top_n: int = 20,
        max_rows: int = MAX_DATA_ROWS,
    ) -> str:
        if method not in {"pearson", "spearman"}:
            raise ValueError("method must be pearson or spearman")

        frame = self.load_table(path, max_rows=max_rows)
        numeric = frame.select_dtypes(include="number")
        if numeric.shape[1] < 2:
            return _to_json({"message": "Need at least two numeric columns for correlations"})

        corr = numeric.corr(method=method)
        if target:
            if target not in numeric.columns:
                raise ValueError(f"Target must be numeric and present in the table: {target}")
            values = (
                corr[target]
                .drop(labels=[target])
                .dropna()
                .sort_values(key=lambda series: series.abs(), ascending=False)
                .head(top_n)
            )
            records = [
                {"column": str(column), "correlation": round(float(value), 6)}
                for column, value in values.items()
            ]
            return _to_json({"method": method, "target": target, "top_correlations": records})

        pairs: list[dict[str, Any]] = []
        columns = list(corr.columns)
        for left_index, left in enumerate(columns):
            for right in columns[left_index + 1 :]:
                value = corr.loc[left, right]
                if pd.isna(value):
                    continue
                pairs.append(
                    {
                        "left": str(left),
                        "right": str(right),
                        "correlation": round(float(value), 6),
                        "abs_correlation": round(abs(float(value)), 6),
                    }
                )
        pairs.sort(key=lambda item: item["abs_correlation"], reverse=True)
        return _to_json({"method": method, "top_pairs": pairs[:top_n]})

    def group_summary(
        self,
        path: str,
        group_by: str,
        value_columns: list[str] | None = None,
        top_n: int = 30,
        max_rows: int = MAX_DATA_ROWS,
    ) -> str:
        frame = self.load_table(path, max_rows=max_rows)
        if group_by not in frame.columns:
            raise ValueError(f"group_by column not found: {group_by}")

        values = value_columns or frame.select_dtypes(include="number").columns.astype(str).tolist()
        values = [column for column in values if column in frame.columns]
        grouped = frame.groupby(group_by, dropna=False)
        output = grouped.size().rename("row_count").reset_index()

        for column in values:
            if pd.api.types.is_numeric_dtype(frame[column]):
                stats = grouped[column].agg(["mean", "median", "sum"]).reset_index()
                stats = stats.rename(
                    columns={
                        "mean": f"{column}_mean",
                        "median": f"{column}_median",
                        "sum": f"{column}_sum",
                    }
                )
                output = output.merge(stats, on=group_by, how="left")

        output = output.sort_values("row_count", ascending=False).head(top_n)
        return _to_json({"group_by": group_by, "summary": _records(output)})

    def outlier_report(
        self,
        path: str,
        columns: list[str] | None = None,
        max_rows: int = MAX_DATA_ROWS,
    ) -> str:
        frame = self.load_table(path, max_rows=max_rows)
        numeric = frame.select_dtypes(include="number")
        selected = columns or numeric.columns.astype(str).tolist()
        records: list[dict[str, Any]] = []

        for column in selected:
            if column not in numeric.columns:
                continue
            series = numeric[column].dropna()
            if series.empty:
                continue
            q1 = series.quantile(0.25)
            q3 = series.quantile(0.75)
            iqr = q3 - q1
            lower = q1 - 1.5 * iqr
            upper = q3 + 1.5 * iqr
            count = int(((series < lower) | (series > upper)).sum())
            records.append(
                {
                    "column": str(column),
                    "lower_bound": round(float(lower), 6),
                    "upper_bound": round(float(upper), 6),
                    "outlier_count": count,
                    "outlier_pct": round(count / len(frame), 4),
                }
            )

        records.sort(key=lambda item: item["outlier_count"], reverse=True)
        return _to_json({"method": "iqr", "outliers": records})

    def target_analysis(
        self,
        path: str,
        target: str,
        max_rows: int = MAX_DATA_ROWS,
        top_n: int = 20,
    ) -> str:
        frame = self.load_table(path, max_rows=max_rows)
        if target not in frame.columns:
            raise ValueError(f"Target column not found: {target}")

        target_series = frame[target]
        payload: dict[str, Any] = {
            "target": target,
            "rows_loaded": int(len(frame)),
            "target_dtype": str(target_series.dtype),
            "target_missing": int(target_series.isna().sum()),
        }

        if pd.api.types.is_numeric_dtype(target_series):
            payload["target_describe"] = _records(target_series.describe().to_frame("value").reset_index())
            payload["correlations"] = json.loads(
                self.correlations(path, target=target, top_n=top_n, max_rows=max_rows)
            )
        else:
            counts = target_series.value_counts(dropna=False).head(top_n).reset_index()
            counts.columns = [target, "count"]
            payload["target_counts"] = _records(counts)

        categorical = frame.select_dtypes(exclude="number").columns.astype(str).tolist()
        categorical = [column for column in categorical if column != target]
        categorical_summary: list[dict[str, Any]] = []
        for column in categorical[:10]:
            grouped = frame.groupby(column, dropna=False)[target]
            if pd.api.types.is_numeric_dtype(target_series):
                summary = grouped.mean().sort_values(ascending=False).head(8)
                categorical_summary.append(
                    {
                        "column": column,
                        "top_groups_by_target_mean": [
                            {"value": str(index), "target_mean": round(float(value), 6)}
                            for index, value in summary.items()
                        ],
                    }
                )
        if categorical_summary:
            payload["categorical_target_summary"] = categorical_summary

        return _to_json(payload)

    def forecast_baseline(
        self,
        path: str,
        date_column: str,
        value_column: str,
        horizon: int = 6,
        freq: str = "MS",
        method: str = "moving_average",
        window: int = 3,
        max_rows: int = MAX_DATA_ROWS,
    ) -> str:
        series = self._time_series(
            path=path,
            date_column=date_column,
            value_column=value_column,
            freq=freq,
            max_rows=max_rows,
        )
        if horizon < 1 or horizon > 365:
            raise ValueError("horizon must be between 1 and 365")
        if method not in {"naive", "moving_average"}:
            raise ValueError("method must be naive or moving_average")

        if method == "naive":
            forecast_value = float(series.iloc[-1])
        else:
            forecast_value = float(series.tail(max(window, 1)).mean())

        future_index = pd.date_range(
            start=series.index[-1] + pd.tseries.frequencies.to_offset(freq),
            periods=horizon,
            freq=freq,
        )
        forecast = [
            {"date": str(date.date()), "forecast": round(forecast_value, 6)}
            for date in future_index
        ]
        return _to_json(
            {
                "method": method,
                "date_column": date_column,
                "value_column": value_column,
                "history_points": int(len(series)),
                "last_observed_date": str(series.index[-1].date()),
                "last_observed_value": round(float(series.iloc[-1]), 6),
                "forecast": forecast,
            }
        )

    def forecast_arima(
        self,
        path: str,
        date_column: str,
        value_column: str,
        horizon: int = 6,
        freq: str = "MS",
        order: list[int] | None = None,
        max_rows: int = MAX_DATA_ROWS,
    ) -> str:
        series = self._time_series(
            path=path,
            date_column=date_column,
            value_column=value_column,
            freq=freq,
            max_rows=max_rows,
        )
        if horizon < 1 or horizon > 365:
            raise ValueError("horizon must be between 1 and 365")
        if len(series) < 8:
            raise ValueError("ARIMA needs at least 8 time points")

        arima_order = tuple(order or [1, 1, 1])
        if len(arima_order) != 3:
            raise ValueError("order must contain [p, d, q]")

        model = ARIMA(series, order=arima_order)
        fitted = model.fit()
        forecast_result = fitted.get_forecast(steps=horizon)
        forecast_mean = forecast_result.predicted_mean
        conf_int = forecast_result.conf_int(alpha=0.2)

        forecast: list[dict[str, Any]] = []
        for date, value in forecast_mean.items():
            lower = conf_int.loc[date].iloc[0]
            upper = conf_int.loc[date].iloc[1]
            forecast.append(
                {
                    "date": str(pd.Timestamp(date).date()),
                    "forecast": round(float(value), 6),
                    "lower_80": round(float(lower), 6),
                    "upper_80": round(float(upper), 6),
                }
            )

        return _to_json(
            {
                "method": "arima",
                "order": list(arima_order),
                "date_column": date_column,
                "value_column": value_column,
                "history_points": int(len(series)),
                "last_observed_date": str(series.index[-1].date()),
                "aic": round(float(fitted.aic), 6),
                "bic": round(float(fitted.bic), 6),
                "forecast": forecast,
                "caveats": [
                    "ARIMA is univariate and does not use external drivers.",
                    "Validate with backtesting before trusting operational decisions.",
                ],
            }
        )

    def forecast_auto_arima(
        self,
        path: str,
        date_column: str,
        value_column: str,
        horizon: int = 6,
        freq: str = "MS",
        seasonal: bool = False,
        max_rows: int = MAX_DATA_ROWS,
    ) -> str:
        try:
            from pmdarima import auto_arima
        except ModuleNotFoundError:
            return _to_json(
                {
                    "available": False,
                    "message": "pmdarima is not installed. Install pmdarima to enable auto_arima.",
                }
            )

        series = self._time_series(
            path=path,
            date_column=date_column,
            value_column=value_column,
            freq=freq,
            max_rows=max_rows,
        )
        if horizon < 1 or horizon > 365:
            raise ValueError("horizon must be between 1 and 365")
        if len(series) < 12:
            raise ValueError("auto_arima needs at least 12 time points")

        model = auto_arima(
            series,
            seasonal=seasonal,
            suppress_warnings=True,
            error_action="ignore",
            stepwise=True,
        )
        values, conf_int = model.predict(n_periods=horizon, return_conf_int=True, alpha=0.2)
        future_index = pd.date_range(
            start=series.index[-1] + pd.tseries.frequencies.to_offset(freq),
            periods=horizon,
            freq=freq,
        )
        forecast = []
        for date, value, interval in zip(future_index, values, conf_int, strict=False):
            forecast.append(
                {
                    "date": str(pd.Timestamp(date).date()),
                    "forecast": round(float(value), 6),
                    "lower_80": round(float(interval[0]), 6),
                    "upper_80": round(float(interval[1]), 6),
                }
            )

        return _to_json(
            {
                "available": True,
                "method": "auto_arima",
                "order": list(model.order),
                "seasonal_order": list(model.seasonal_order),
                "aic": round(float(model.aic()), 6),
                "forecast": forecast,
            }
        )

    def forecast_prophet(
        self,
        path: str,
        date_column: str,
        value_column: str,
        horizon: int = 6,
        freq: str = "MS",
        max_rows: int = MAX_DATA_ROWS,
    ) -> str:
        try:
            from prophet import Prophet
        except ModuleNotFoundError:
            return _to_json(
                {
                    "available": False,
                    "message": "Prophet is not installed. Install prophet to enable this tool.",
                }
            )

        series = self._time_series(
            path=path,
            date_column=date_column,
            value_column=value_column,
            freq=freq,
            max_rows=max_rows,
        )
        frame = series.reset_index()
        frame.columns = ["ds", "y"]
        model = Prophet()
        model.fit(frame)
        future = model.make_future_dataframe(periods=horizon, freq=freq)
        forecast_frame = model.predict(future).tail(horizon)
        forecast = [
            {
                "date": str(pd.Timestamp(row["ds"]).date()),
                "forecast": round(float(row["yhat"]), 6),
                "lower": round(float(row["yhat_lower"]), 6),
                "upper": round(float(row["yhat_upper"]), 6),
            }
            for _, row in forecast_frame.iterrows()
        ]
        return _to_json(
            {
                "available": True,
                "method": "prophet",
                "history_points": int(len(series)),
                "forecast": forecast,
            }
        )

    def _time_series(
        self,
        path: str,
        date_column: str,
        value_column: str,
        freq: str,
        max_rows: int,
    ) -> pd.Series:
        frame = self.load_table(path, max_rows=max_rows)
        if date_column not in frame.columns:
            raise ValueError(f"date_column not found: {date_column}")
        if value_column not in frame.columns:
            raise ValueError(f"value_column not found: {value_column}")

        dates = pd.to_datetime(frame[date_column], errors="coerce")
        values = pd.to_numeric(frame[value_column], errors="coerce")
        ts_frame = pd.DataFrame({"date": dates, "value": values}).dropna()
        if ts_frame.empty:
            raise ValueError("No valid date/value rows found")

        series = (
            ts_frame.set_index("date")["value"]
            .sort_index()
            .resample(freq)
            .sum()
            .asfreq(freq)
        )
        series = series.interpolate(limit_direction="both")
        if series.empty:
            raise ValueError("No time series points after resampling")
        return series

    def predictive_model(
        self,
        path: str,
        target: str,
        problem_type: str = "auto",
        model: str = "random_forest",
        date_column: str | None = None,
        exclude_columns: list[str] | None = None,
        test_size: float = 0.2,
        max_rows: int = MAX_DATA_ROWS,
    ) -> str:
        from sklearn.compose import ColumnTransformer
        from sklearn.impute import SimpleImputer
        from sklearn.inspection import permutation_importance
        from sklearn.metrics import (
            accuracy_score,
            f1_score,
            mean_absolute_error,
            mean_squared_error,
            r2_score,
            roc_auc_score,
        )
        from sklearn.model_selection import train_test_split
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import LabelEncoder, OneHotEncoder, StandardScaler

        frame = self.load_table(path, max_rows=max_rows).copy()
        if target not in frame.columns:
            raise ValueError(f"Target column not found: {target}")
        if not 0.05 <= test_size <= 0.5:
            raise ValueError("test_size must be between 0.05 and 0.5")

        excluded = set(exclude_columns or [])
        excluded.add(target)
        if date_column:
            if date_column not in frame.columns:
                raise ValueError(f"date_column not found: {date_column}")
            frame = frame.sort_values(date_column)
            excluded.add(date_column)

        frame = frame.dropna(subset=[target])
        y = frame[target]
        x = frame[[column for column in frame.columns if column not in excluded]]
        if x.empty:
            raise ValueError("No feature columns available after exclusions")

        resolved_problem = _resolve_problem_type(y, problem_type)
        numeric_features = x.select_dtypes(include="number").columns.astype(str).tolist()
        categorical_features = [column for column in x.columns.astype(str) if column not in numeric_features]

        preprocessor = ColumnTransformer(
            transformers=[
                (
                    "numeric",
                    Pipeline(
                        steps=[
                            ("imputer", SimpleImputer(strategy="median")),
                            ("scaler", StandardScaler()),
                        ]
                    ),
                    numeric_features,
                ),
                (
                    "categorical",
                    Pipeline(
                        steps=[
                            ("imputer", SimpleImputer(strategy="most_frequent")),
                            ("onehot", OneHotEncoder(handle_unknown="ignore", max_categories=30)),
                        ]
                    ),
                    categorical_features,
                ),
            ]
        )

        estimator = _make_estimator(resolved_problem, model)
        pipeline = Pipeline(steps=[("preprocess", preprocessor), ("model", estimator)])

        if date_column:
            split_index = max(1, int(len(frame) * (1 - test_size)))
            x_train, x_test = x.iloc[:split_index], x.iloc[split_index:]
            y_train, y_test = y.iloc[:split_index], y.iloc[split_index:]
        else:
            stratify = y if resolved_problem == "classification" and y.nunique() < len(y) * 0.5 else None
            x_train, x_test, y_train, y_test = train_test_split(
                x,
                y,
                test_size=test_size,
                random_state=42,
                stratify=stratify,
            )

        if len(x_test) == 0:
            raise ValueError("Test split is empty")

        class_labels: list[str] | None = None
        if resolved_problem == "classification":
            encoder = LabelEncoder()
            y_train = pd.Series(encoder.fit_transform(y_train), index=y_train.index)
            y_test = pd.Series(encoder.transform(y_test), index=y_test.index)
            class_labels = [str(label) for label in encoder.classes_]

        pipeline.fit(x_train, y_train)
        predictions = pipeline.predict(x_test)

        payload: dict[str, Any] = {
            "problem_type": resolved_problem,
            "model": model,
            "rows_used": int(len(frame)),
            "train_rows": int(len(x_train)),
            "test_rows": int(len(x_test)),
            "features": list(map(str, x.columns)),
            "numeric_features": numeric_features,
            "categorical_features": categorical_features,
            "split": "time_ordered" if date_column else "random_seed_42",
        }
        if class_labels is not None:
            payload["class_labels"] = class_labels

        if resolved_problem == "classification":
            payload["metrics"] = {
                "accuracy": round(float(accuracy_score(y_test, predictions)), 6),
                "f1_weighted": round(float(f1_score(y_test, predictions, average="weighted")), 6),
            }
            if hasattr(pipeline, "predict_proba") and y.nunique() == 2:
                probabilities = pipeline.predict_proba(x_test)[:, 1]
                payload["metrics"]["roc_auc"] = round(float(roc_auc_score(y_test, probabilities)), 6)
        else:
            rmse = mean_squared_error(y_test, predictions) ** 0.5
            payload["metrics"] = {
                "mae": round(float(mean_absolute_error(y_test, predictions)), 6),
                "rmse": round(float(rmse), 6),
                "r2": round(float(r2_score(y_test, predictions)), 6),
            }

        try:
            importance = permutation_importance(
                pipeline,
                x_test,
                y_test,
                n_repeats=5,
                random_state=42,
                scoring="r2" if resolved_problem == "regression" else "accuracy",
            )
            ranking = sorted(
                zip(x.columns.astype(str), importance.importances_mean, strict=False),
                key=lambda item: abs(item[1]),
                reverse=True,
            )
            payload["permutation_importance"] = [
                {"feature": feature, "importance": round(float(value), 6)}
                for feature, value in ranking[:20]
            ]
        except Exception as exc:  # pragma: no cover - diagnostic fallback
            payload["permutation_importance_error"] = str(exc)

        payload["caveats"] = [
            "This is an exploratory model, not production validation.",
            "Check leakage, time ordering, target definition, and business stability before use.",
        ]
        return _to_json(payload)

    def model_availability(self, deep_check: bool = False) -> str:
        checks = {
            "statsmodels_arima": ("statsmodels", "ARIMA forecasting"),
            "pmdarima_auto_arima": ("pmdarima", "automatic ARIMA order search"),
            "prophet": ("prophet", "trend/seasonality forecasting"),
            "sklearn": ("sklearn", "classical ML pipelines"),
            "xgboost": ("xgboost", "gradient boosted trees"),
            "catboost": ("catboost", "categorical boosted trees"),
            "lightgbm": ("lightgbm", "gradient boosted trees"),
            "optuna": ("optuna", "hyperparameter optimization"),
            "shap": ("shap", "model explainability"),
            "sktime": ("sktime", "time-series toolkit"),
        }
        availability = []
        for name, (module, purpose) in checks.items():
            if not deep_check:
                availability.append(
                    {
                        "name": name,
                        "available": importlib.util.find_spec(module) is not None,
                        "version": "not_imported",
                        "purpose": purpose,
                    }
                )
                continue

            try:
                imported = __import__(module)
                availability.append(
                    {
                        "name": name,
                        "available": True,
                        "version": str(getattr(imported, "__version__", "unknown")),
                        "purpose": purpose,
                    }
                )
            except Exception as exc:
                availability.append(
                    {
                        "name": name,
                        "available": False,
                        "error": str(exc),
                        "purpose": purpose,
                    }
                )
        return _to_json({"deep_check": deep_check, "models": availability})


def _datetime_ranges(frame: pd.DataFrame) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []
    for column in frame.columns:
        series = frame[column]
        if not pd.api.types.is_datetime64_any_dtype(series):
            parsed = pd.to_datetime(series, errors="coerce")
            if parsed.notna().mean() < 0.8:
                continue
            series = parsed
        non_null = series.dropna()
        if non_null.empty:
            continue
        ranges.append(
            {
                "column": str(column),
                "min": str(non_null.min()),
                "max": str(non_null.max()),
            }
        )
    return ranges


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    clean = frame.where(pd.notna(frame), None)
    return clean.to_dict(orient="records")


def _to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _resolve_problem_type(target: pd.Series, problem_type: str) -> str:
    if problem_type not in {"auto", "classification", "regression"}:
        raise ValueError("problem_type must be auto, classification, or regression")
    if problem_type != "auto":
        return problem_type
    if pd.api.types.is_numeric_dtype(target) and target.nunique(dropna=True) > 12:
        return "regression"
    return "classification"


def _make_estimator(problem_type: str, model: str):
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.linear_model import LinearRegression, LogisticRegression

    if problem_type == "classification":
        if model == "xgboost":
            from xgboost import XGBClassifier

            return XGBClassifier(
                n_estimators=160,
                max_depth=4,
                learning_rate=0.05,
                random_state=42,
                n_jobs=1,
                eval_metric="logloss",
            )
        if model == "catboost":
            from catboost import CatBoostClassifier

            return CatBoostClassifier(iterations=160, learning_rate=0.05, depth=5, verbose=False)
        if model == "lightgbm":
            from lightgbm import LGBMClassifier

            return LGBMClassifier(n_estimators=160, learning_rate=0.05, random_state=42, n_jobs=1)
        if model == "logistic_regression":
            return LogisticRegression(max_iter=1000)
        if model in {"random_forest", "auto"}:
            return RandomForestClassifier(n_estimators=120, random_state=42, n_jobs=1)
        raise ValueError(
            "classification model must be random_forest, logistic_regression, xgboost, catboost, or lightgbm"
        )

    if model == "xgboost":
        from xgboost import XGBRegressor

        return XGBRegressor(
            n_estimators=160,
            max_depth=4,
            learning_rate=0.05,
            random_state=42,
            n_jobs=1,
        )
    if model == "catboost":
        from catboost import CatBoostRegressor

        return CatBoostRegressor(iterations=160, learning_rate=0.05, depth=5, verbose=False)
    if model == "lightgbm":
        from lightgbm import LGBMRegressor

        return LGBMRegressor(n_estimators=160, learning_rate=0.05, random_state=42, n_jobs=1)
    if model == "linear_regression":
        return LinearRegression()
    if model in {"random_forest", "auto"}:
        return RandomForestRegressor(n_estimators=120, random_state=42, n_jobs=1)
    raise ValueError(
        "regression model must be random_forest, linear_regression, xgboost, catboost, or lightgbm"
    )
