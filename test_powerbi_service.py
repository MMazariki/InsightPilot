
import json

from src.powerbi_query_service import (
    PowerBIQueryService,
    PowerBIQueryError,
)


def main():

    powerbi = PowerBIQueryService()

    try:

        print("\nTesting PowerBIQueryService...")

        result = powerbi.execute_dax(
            """
            EVALUATE
            ROW(
                "Net Sales SAR", [Net Sales],
                "Gross Profit SAR", [Gross Profit],
                "Gross Margin %", [Gross Margin %],
                "Distinct Orders",
                    DISTINCTCOUNT(FactSales[OrderID])
            )
            """
        )

        print("\nQUERY RESULTS")
        print("=" * 60)

        print(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
            )
        )

        print("\nPower BI Query Service working.")

    except PowerBIQueryError as exc:

        print(f"\nPower BI error: {exc}")

        if exc.status_code:
            print("HTTP status:", exc.status_code)

    finally:
        powerbi.close()


if __name__ == "__main__":
    main()
