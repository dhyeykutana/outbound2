/**
* Theme: Skotwind - Tailwind CSS  Admin Layout & UI Kit Template
* Author: MyraStudio
* Module/App: dashboard js
*/

import ApexCharts from "apexcharts";

"use strict";

// chart6 (Line chart)
var options = {
    chart: {
        height: 322,
        type: "area",
        toolbar: {
            show: false,
        },
        dropShadow: {
            enabled: true,
            top: 12,
            left: 0,
            bottom: 0,
            right: 0,
            blur: 2,
            color: "rgba(132, 145, 183, 0.3)",
            opacity: 0.35,
        },
    },
    colors: ["#f472b6", "#38bdf8"], // Tailwind's Pink and Sky shades
    dataLabels: {
        enabled: false,
    },
    stroke: {
        show: true,
        curve: "smooth",
        width: [2, 2],
        dashArray: [0, 4],
        lineCap: "round",
    },
    series: [
        {
            name: "Income",
            data: [60, 40, 80, 50, 95, 65, 100, 70, 120, 85, 140, 90],
        },
        {
            name: "Expenses",
            data: [25, 50, 30, 55, 35, 70, 45, 85, 55, 95, 70, 105],
        },
    ],

    labels: [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ],

    yaxis: {

        labels: {
            formatter: function (value) {
                return value + "k";
            },
            offsetX: -12,
            offsetY: 0,
        },
    },
    grid: {
        strokeDashArray: 3,
        xaxis: {
            lines: {
                show: true,
            },
        },
        yaxis: {
            lines: {
                show: false,
            },
        },
    },
    legend: {
        show: false,
    },

    fill: {
        type: "gradient",
        gradient: {
            type: "vertical",
            shadeIntensity: 1,
            inverseColors: !1,
            opacityFrom: 0.05,
            opacityTo: 0.05,
            stops: [45, 100],
        },
    },
};

var lineChartEl = document.querySelector("#line-chart");
if (lineChartEl) {
    var chart = new ApexCharts(lineChartEl, options);
    chart.render();
}


var options2 = {
    chart: {
        type: 'bar',
        height: 320,
        stacked: true,
        toolbar: { show: false }
    },
    colors: ['#f43f5e', '#14b8a6'], // rose, teal
    series: [
        {
            name: 'Profit',
            data: [15, 18, 21, 25, 23, 28, 31, 33, 36, 40]
        },
        {
            name: 'Earnings',
            data: [22, 25, 28, 30, 34, 38, 41, 46, 52, 57]
        }
    ],
    xaxis: {
        categories: ['2015', '2016', '2017', '2018', '2019', '2020', '2021', '2022', '2023', '2024'],
        axisBorder: { show: false },
        axisTicks: { show: false },
        labels: { style: { fontSize: '13px' } }
    },
    yaxis: {
        labels: {
            formatter: (val) => `$${val}k`,
            style: { fontSize: '12px' }
        }
    },
    plotOptions: {
        bar: {
            horizontal: false,
            columnWidth: '55%',
            borderRadius: 5
        }
    },
   
    grid: {
        borderColor: '#e5e7eb',
        strokeDashArray: 4
    },
    legend: { show: false }
};

var revenueChartEl = document.querySelector("#revenueChart");
if (revenueChartEl) {
    const chart2 = new ApexCharts(revenueChartEl, options2);
    chart2.render();
}