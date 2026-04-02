import { Calendar } from "@fullcalendar/core";
import dayGridPlugin from '@fullcalendar/daygrid';
import timeGridPlugin from '@fullcalendar/timegrid'
import listPlugin from '@fullcalendar/list'


class calendar {

    constructor() {
        this.body = document.body;
        this.calendar = document.getElementById('calendar');
        this.formEvent = document.getElementById('forms-event');
        this.btnNewEvent = document.getElementById('btn-new-event');
        this.btnSaveEvent = document.getElementById('btn-save-event');
        this.modalTitle = document.getElementById('modal-title');
        this.calendarObj = null;
        this.selectedEvent = null;
        this.newEventData = null;
    }

    onEventClick(info) {
        this.formEvent?.reset();
        this.formEvent.classList.remove('was-validated');
        this.newEventData = null;
        this.modalTitle.text = ('Edit Event');
        this.modal.show();
        this.selectedEvent = info.event;
        document.getElementById('event-title').value = this.selectedEvent.title;
        document.getElementById('event-category').value = (this.selectedEvent.classNames[0]);
    }

    onSelect(info) {
        this.formEvent?.reset();
        this.formEvent?.classList.remove('was-validated');
        this.selectedEvent = null;
        this.newEventData = info;
        this.modalTitle.text = ('Add New Event');
        this.modal.show();
        this.calendarObj.unselect();
    }

    init() {
        /*  Initialize the calendar  */
        const today = new Date();
        const self = this;

        const defaultEvents = [{
            title: 'Interview - Backend Engineer',
            start: today,
            end: today,
            className: 'bg-primary'
        },
        {
            title: 'Meeting with CT Team',
            start: new Date(Date.now() + 13000000),
            end: today,
            className: 'bg-warning'
        },
        {
            title: 'Meeting with Mr. Shield',
            start: new Date(Date.now() + 308000000),
            end: new Date(Date.now() + 338000000),
            className: 'bg-info'
        },
        {
            title: 'Interview - Frontend Engineer',
            start: new Date(Date.now() + 60570000),
            end: new Date(Date.now() + 153000000),
            className: 'bg-secondary'
        },
        {
            title: 'Phone Screen - Frontend Engineer',
            start: new Date(Date.now() + 168000000),
            className: 'bg-success'
        },
        {
            title: 'Buy Design Assets',
            start: new Date(Date.now() + 330000000),
            end: new Date(Date.now() + 330800000),
            className: 'bg-primary',
        },
        {
            title: 'Setup Github Repository',
            start: new Date(Date.now() + 1008000000),
            end: new Date(Date.now() + 1108000000),
            className: 'bg-danger'
        },
        {
            title: 'Meeting with Mr. Shreyu',
            start: new Date(Date.now() + 2508000000),
            end: new Date(Date.now() + 2508000000),
            className: 'bg-dark'
        }];

        // cal - init
        self.calendarObj = new Calendar(self.calendar, {

            plugins: [dayGridPlugin, timeGridPlugin, listPlugin ],
            slotDuration: '00:30:00', /* If we want to split day time each 15minutes */
            slotMinTime: '07:00:00',
            slotMaxTime: '19:00:00',
            themeSystem: 'default',
            buttonText: {
                today: 'Today',
                month: 'Month',
                week: 'Week',
                day: 'Day',
                list: 'List',
                prev: 'Prev',
                next: 'Next'
            },
            initialView: 'dayGridMonth',
            handleWindowResize: true,
            height: window.innerHeight - 300,
            headerToolbar: {
                left: 'prev,next today',
                center: 'title',
                right: 'dayGridMonth,timeGridWeek,timeGridDay,listMonth'
            },
            initialEvents: defaultEvents,
            editable: true,
            droppable: true, // this allows things to be dropped onto the calendar !!!
            // dayMaxEventRows: false, // allow "more" link when too many events
            selectable: true,
            dateClick: function (info) {
                self.onSelect(info);
            },
            eventClick: function (info) {
                self.onEventClick(info);
            }
        });

        self.calendarObj.render();

    }

}
document.addEventListener('DOMContentLoaded', function (e) {
    new calendar().init();
});