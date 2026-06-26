--
-- PostgreSQL database dump
--

\restrict GrEgT1k0ahkh5xSR1oMNvOcb1wjd9OB0wXehVviy7cCQKdQem2QUM2Pe9AFbXFS

-- Dumped from database version 18.4 (Homebrew)
-- Dumped by pg_dump version 18.4 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: active_sessions; Type: TABLE; Schema: public; Owner: ansh
--

CREATE TABLE public.active_sessions (
    employee_id text NOT NULL,
    token text,
    login_time timestamp with time zone DEFAULT now()
);


ALTER TABLE public.active_sessions OWNER TO ansh;

--
-- Name: activity_logs; Type: TABLE; Schema: public; Owner: ansh
--

CREATE TABLE public.activity_logs (
    id integer NOT NULL,
    employee_id character varying(50),
    activity text,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.activity_logs OWNER TO ansh;

--
-- Name: activity_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: ansh
--

CREATE SEQUENCE public.activity_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.activity_logs_id_seq OWNER TO ansh;

--
-- Name: activity_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ansh
--

ALTER SEQUENCE public.activity_logs_id_seq OWNED BY public.activity_logs.id;


--
-- Name: attendance; Type: TABLE; Schema: public; Owner: ansh
--

CREATE TABLE public.attendance (
    id integer NOT NULL,
    employee_id character varying(50) NOT NULL,
    login_time timestamp without time zone NOT NULL,
    logout_time timestamp without time zone,
    total_hours interval,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.attendance OWNER TO ansh;

--
-- Name: attendance_id_seq; Type: SEQUENCE; Schema: public; Owner: ansh
--

CREATE SEQUENCE public.attendance_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.attendance_id_seq OWNER TO ansh;

--
-- Name: attendance_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ansh
--

ALTER SEQUENCE public.attendance_id_seq OWNED BY public.attendance.id;


--
-- Name: employee_configs; Type: TABLE; Schema: public; Owner: ansh
--

CREATE TABLE public.employee_configs (
    id integer NOT NULL,
    employee_id character varying(50),
    screenshot_min_minutes integer DEFAULT 3 NOT NULL,
    screenshot_max_minutes integer DEFAULT 10 NOT NULL,
    screenshot_count integer DEFAULT 3 NOT NULL,
    upload_interval_minutes integer DEFAULT 60 NOT NULL,
    idle_threshold_seconds integer DEFAULT 60 NOT NULL,
    force_logout boolean DEFAULT false NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.employee_configs OWNER TO ansh;

--
-- Name: employee_configs_id_seq; Type: SEQUENCE; Schema: public; Owner: ansh
--

CREATE SEQUENCE public.employee_configs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.employee_configs_id_seq OWNER TO ansh;

--
-- Name: employee_configs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ansh
--

ALTER SEQUENCE public.employee_configs_id_seq OWNED BY public.employee_configs.id;


--
-- Name: employees; Type: TABLE; Schema: public; Owner: ansh
--

CREATE TABLE public.employees (
    id integer NOT NULL,
    employee_id character varying(50),
    username character varying(100),
    password character varying(255),
    role character varying(50),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.employees OWNER TO ansh;

--
-- Name: employees_id_seq; Type: SEQUENCE; Schema: public; Owner: ansh
--

CREATE SEQUENCE public.employees_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.employees_id_seq OWNER TO ansh;

--
-- Name: employees_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ansh
--

ALTER SEQUENCE public.employees_id_seq OWNED BY public.employees.id;


--
-- Name: screenshots; Type: TABLE; Schema: public; Owner: ansh
--

CREATE TABLE public.screenshots (
    id integer NOT NULL,
    employee_id character varying(50),
    file_name text,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.screenshots OWNER TO ansh;

--
-- Name: screenshots_id_seq; Type: SEQUENCE; Schema: public; Owner: ansh
--

CREATE SEQUENCE public.screenshots_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.screenshots_id_seq OWNER TO ansh;

--
-- Name: screenshots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ansh
--

ALTER SEQUENCE public.screenshots_id_seq OWNED BY public.screenshots.id;


--
-- Name: activity_logs id; Type: DEFAULT; Schema: public; Owner: ansh
--

ALTER TABLE ONLY public.activity_logs ALTER COLUMN id SET DEFAULT nextval('public.activity_logs_id_seq'::regclass);


--
-- Name: attendance id; Type: DEFAULT; Schema: public; Owner: ansh
--

ALTER TABLE ONLY public.attendance ALTER COLUMN id SET DEFAULT nextval('public.attendance_id_seq'::regclass);


--
-- Name: employee_configs id; Type: DEFAULT; Schema: public; Owner: ansh
--

ALTER TABLE ONLY public.employee_configs ALTER COLUMN id SET DEFAULT nextval('public.employee_configs_id_seq'::regclass);


--
-- Name: employees id; Type: DEFAULT; Schema: public; Owner: ansh
--

ALTER TABLE ONLY public.employees ALTER COLUMN id SET DEFAULT nextval('public.employees_id_seq'::regclass);


--
-- Name: screenshots id; Type: DEFAULT; Schema: public; Owner: ansh
--

ALTER TABLE ONLY public.screenshots ALTER COLUMN id SET DEFAULT nextval('public.screenshots_id_seq'::regclass);


--
-- Name: active_sessions active_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: ansh
--

ALTER TABLE ONLY public.active_sessions
    ADD CONSTRAINT active_sessions_pkey PRIMARY KEY (employee_id);


--
-- Name: activity_logs activity_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: ansh
--

ALTER TABLE ONLY public.activity_logs
    ADD CONSTRAINT activity_logs_pkey PRIMARY KEY (id);


--
-- Name: attendance attendance_pkey; Type: CONSTRAINT; Schema: public; Owner: ansh
--

ALTER TABLE ONLY public.attendance
    ADD CONSTRAINT attendance_pkey PRIMARY KEY (id);


--
-- Name: employee_configs employee_configs_employee_id_key; Type: CONSTRAINT; Schema: public; Owner: ansh
--

ALTER TABLE ONLY public.employee_configs
    ADD CONSTRAINT employee_configs_employee_id_key UNIQUE (employee_id);


--
-- Name: employee_configs employee_configs_pkey; Type: CONSTRAINT; Schema: public; Owner: ansh
--

ALTER TABLE ONLY public.employee_configs
    ADD CONSTRAINT employee_configs_pkey PRIMARY KEY (id);


--
-- Name: employees employees_employee_id_key; Type: CONSTRAINT; Schema: public; Owner: ansh
--

ALTER TABLE ONLY public.employees
    ADD CONSTRAINT employees_employee_id_key UNIQUE (employee_id);


--
-- Name: employees employees_pkey; Type: CONSTRAINT; Schema: public; Owner: ansh
--

ALTER TABLE ONLY public.employees
    ADD CONSTRAINT employees_pkey PRIMARY KEY (id);


--
-- Name: employees employees_username_key; Type: CONSTRAINT; Schema: public; Owner: ansh
--

ALTER TABLE ONLY public.employees
    ADD CONSTRAINT employees_username_key UNIQUE (username);


--
-- Name: screenshots screenshots_pkey; Type: CONSTRAINT; Schema: public; Owner: ansh
--

ALTER TABLE ONLY public.screenshots
    ADD CONSTRAINT screenshots_pkey PRIMARY KEY (id);


--
-- Name: idx_active_sessions_employee; Type: INDEX; Schema: public; Owner: ansh
--

CREATE INDEX idx_active_sessions_employee ON public.active_sessions USING btree (employee_id);


--
-- Name: idx_activity_logs_employee_id; Type: INDEX; Schema: public; Owner: ansh
--

CREATE INDEX idx_activity_logs_employee_id ON public.activity_logs USING btree (employee_id);


--
-- Name: idx_attendance_employee_id; Type: INDEX; Schema: public; Owner: ansh
--

CREATE INDEX idx_attendance_employee_id ON public.attendance USING btree (employee_id);


--
-- Name: idx_screenshots_created_at; Type: INDEX; Schema: public; Owner: ansh
--

CREATE INDEX idx_screenshots_created_at ON public.screenshots USING btree (created_at);


--
-- Name: idx_screenshots_employee_id; Type: INDEX; Schema: public; Owner: ansh
--

CREATE INDEX idx_screenshots_employee_id ON public.screenshots USING btree (employee_id);


--
-- Name: activity_logs fk_activity_logs_employee; Type: FK CONSTRAINT; Schema: public; Owner: ansh
--

ALTER TABLE ONLY public.activity_logs
    ADD CONSTRAINT fk_activity_logs_employee FOREIGN KEY (employee_id) REFERENCES public.employees(employee_id) ON DELETE CASCADE;


--
-- Name: attendance fk_attendance_employee; Type: FK CONSTRAINT; Schema: public; Owner: ansh
--

ALTER TABLE ONLY public.attendance
    ADD CONSTRAINT fk_attendance_employee FOREIGN KEY (employee_id) REFERENCES public.employees(employee_id) ON DELETE CASCADE;


--
-- Name: screenshots fk_screenshots_employee; Type: FK CONSTRAINT; Schema: public; Owner: ansh
--

ALTER TABLE ONLY public.screenshots
    ADD CONSTRAINT fk_screenshots_employee FOREIGN KEY (employee_id) REFERENCES public.employees(employee_id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict GrEgT1k0ahkh5xSR1oMNvOcb1wjd9OB0wXehVviy7cCQKdQem2QUM2Pe9AFbXFS

